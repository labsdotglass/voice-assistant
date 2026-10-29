"""Draft transcripts while the user is still talking.

About once a second during speech, the audio so far is re-transcribed and sent to the mod
as a draft line. The final full pass (Pipecat's segmented STT, Whisper large-v3 on the GPU,
on end of speech) then replaces it.

Drafts use a small Whisper on the CPU (faster-whisper base.en, ~0.25 s a draft), so they
never hold the GPU: the final pass starts the moment you stop, instead of waiting for an
in-flight draft (up to ~0.3 s when they shared large-v3 behind a lock).
"""

import asyncio
import threading
import time

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pipecat.frames.frames import TranscriptionFrame

from helper_transport import collapse_runs
from talker import SAID, SPEECH_READY, emit

DRAFT_MODEL = "base.en"  # faster-whisper, on the CPU
OTHER_LANGUAGES_DRAFT_MODEL = "small"  # multilingual; base is too rough for Hebrew
DRAFT = {"language": "en", "model": DRAFT_MODEL}  # the menu's language
_drafter = None  # loaded on first use, off the event loop
_drafter_model = None
_drafter_lock = threading.Lock()


def set_draft_language(code: str):
    DRAFT["language"] = code
    DRAFT["model"] = DRAFT_MODEL if code == "en" else OTHER_LANGUAGES_DRAFT_MODEL


def _load_drafter():
    global _drafter, _drafter_model
    if _drafter is None or _drafter_model != DRAFT["model"]:
        from faster_whisper import WhisperModel

        _drafter_model = DRAFT["model"]
        _drafter = WhisperModel(_drafter_model, device="cpu", compute_type="int8")


def load_speech_models(final_model: str, also=(), strict: bool = False):
    """Loads both Whisper models up front (each would otherwise load on your first words), and anything in
    also (Hebrew's voice, or MLX Kokoro for an English one), then sets SPEECH_READY.

    Runs in a thread at start-up. The first start downloads them, which takes minutes; later starts a few seconds.
    Only the language's own models: its Whisper, its draft model, its voice. strict (a first run's setup, and
    setup.py): a failure is raised, and SPEECH_READY stays clear, so the window can offer Retry.
    """
    started = time.monotonic()
    try:
        with _drafter_lock:
            _load_drafter()
        import mlx_whisper

        mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo=final_model)  # loads and compiles it
        for load in also:
            load()
        logger.info(f"[lifecycle] speech models loaded in {time.monotonic() - started:.1f}s")
    except Exception as e:
        if strict:
            logger.error(f"[lifecycle] couldn't download or load the speech models ({e})")
            raise
        logger.error(f"[lifecycle] couldn't load the speech models up front ({e}); they load on first use")
    SPEECH_READY.set()


def _draft(audio: np.ndarray) -> str:
    with _drafter_lock:  # one draft at a time; the final pass doesn't take this
        _load_drafter()
        segments, _ = _drafter.transcribe(audio, language=DRAFT["language"], beam_size=1)
        return collapse_runs(" ".join(seg.text.strip() for seg in segments).strip())


class InterimTranscriber(FrameProcessor):
    """Sits right after the audio input; sees the mic audio going down and VAD frames coming up."""

    PREROLL_SECS = 0.5  # VAD fires a little after speech starts
    MIN_SECS = 0.6  # too little audio makes Whisper guess

    def __init__(self, interval: float = 1.0, **kwargs):
        super().__init__(**kwargs)
        self._interval = interval
        self._recent = bytearray()  # rolling pre-roll while silent
        self._speech = bytearray()
        self._rate = 16000
        self._speaking = False
        self._loop: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self._rate = frame.sample_rate
            if self._speaking:
                self._speech += frame.audio
            else:
                self._recent += frame.audio
                del self._recent[: max(0, len(self._recent) - int(self.PREROLL_SECS * self._rate) * 2)]
        elif isinstance(frame, VADUserStartedSpeakingFrame) and not self._speaking:
            self._speaking = True
            self._speech = bytearray(self._recent)
            self._loop = self.create_task(self._drafts())
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and self._speaking:
            self._speaking = False
            if self._loop:
                await self.cancel_task(self._loop)
                self._loop = None
        await self.push_frame(frame, direction)

    async def _drafts(self):
        last = ""
        while self._speaking:
            await asyncio.sleep(self._interval)
            if not self._speaking or len(self._speech) < self.MIN_SECS * self._rate * 2:
                continue
            audio = np.frombuffer(bytes(self._speech), dtype=np.int16).astype(np.float32) / 32768.0
            t0 = time.monotonic()
            text = await asyncio.to_thread(_draft, audio)
            if self._speaking and text and text != last:
                last = text
                emit({"type": "draft", "text": " ".join([*SAID, text])})  # the pieces before this one stay
                logger.debug(f"draft ({time.monotonic() - t0:.2f}s, {len(audio) / self._rate:.1f}s audio): {text}")


class SaidSoFar(FrameProcessor):
    """After Whisper and the filters: keeps each finished piece of your turn for the draft bubble."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            SAID.append(frame.text.strip())
            emit({"type": "draft", "text": " ".join(SAID)})
        await self.push_frame(frame, direction)
