"""A finished question ends your turn at once, even when smart-turn hears it as unfinished (no model calls).

"What time zone is Tokyo in?" in macOS's Samantha voice: smart-turn v3 judges it unfinished and Pipecat's
default waits out 3 s of silence. With QuestionEndsTurn, Whisper's "?" ends the turn as soon as it's in;
without a question mark, the wait stays, so a pause mid-sentence still isn't answered.
"""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import InputAudioRawFrame, TranscriptionFrame, UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.time import time_now_iso8601

from helper_transport import QuestionEndsTurn

RATE, CHUNK = 16000, 320  # 20 ms, like the mic
WHISPER_SECS = 0.2  # Whisper's text arrives about this long after VAD's stop, as on the bench
results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def spoken(text: str) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp) / "q.aiff", Path(tmp) / "q.wav"
        subprocess.run(["say", "-v", "Samantha", "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
        audio, _ = sf.read(wav, dtype="float32")
    return audio


class Watch(BaseObserver):
    def __init__(self):
        super().__init__()
        self.vad_stop = self.turn_end = None

    async def on_push_frame(self, data: FramePushed):
        if isinstance(data.frame, VADUserStoppedSpeakingFrame) and self.vad_stop is None:
            self.vad_stop = time.monotonic()
        elif isinstance(data.frame, UserStoppedSpeakingFrame) and self.turn_end is None:
            self.turn_end = time.monotonic()


async def turn_end_after(audio: np.ndarray, heard: str, stop) -> float | None:
    """Seconds from the end of the audio to the end of the turn, with Whisper's text `heard`."""
    params = LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    if stop:
        params.user_turn_strategies = UserTurnStrategies(stop=[stop])
    user, _ = LLMContextAggregatorPair(LLMContext(), user_params=params)
    watch = Watch()
    task = PipelineTask(Pipeline([user]), observers=[watch], idle_timeout_secs=None)

    async def feed():
        await asyncio.sleep(0.5)
        audio_out = np.concatenate([np.zeros(RATE // 2, np.float32), audio, np.zeros(RATE * 5, np.float32)])
        clock, said_at, sent = time.monotonic(), None, False
        for i in range(0, len(audio_out), CHUNK):
            if i >= RATE // 2 + len(audio) and said_at is None:
                said_at = time.monotonic()
            pcm = (np.clip(audio_out[i : i + CHUNK], -1, 1) * 32767).astype(np.int16).tobytes()
            await task.queue_frame(InputAudioRawFrame(audio=pcm, sample_rate=RATE, num_channels=1))
            if watch.vad_stop and not sent and time.monotonic() - watch.vad_stop >= WHISPER_SECS:
                sent = True
                await task.queue_frame(TranscriptionFrame(text=heard, user_id="", timestamp=time_now_iso8601(), finalized=True))
            if watch.turn_end:
                break
            clock += CHUNK / RATE
            await asyncio.sleep(max(0, clock - time.monotonic()))
        await task.cancel()
        return (watch.turn_end - said_at) if watch.turn_end and said_at else None

    runner = PipelineRunner(handle_sigint=False)
    _, took = await asyncio.gather(runner.run(task), feed())
    return took


async def main():
    logger.remove()
    tokyo = spoken("What time zone is Tokyo in?")
    before = await turn_end_after(tokyo, "What time zone is Tokyo in?", None)
    ok(before is not None and before > 2.5, f"Pipecat's default waits out smart-turn's 3 s on it: {before and round(before, 2)} s")
    after = await turn_end_after(tokyo, "What time zone is Tokyo in?", QuestionEndsTurn())
    ok(after is not None and after < 1.0, f"with QuestionEndsTurn, Whisper's '?' ends it: {after and round(after, 2)} s")
    unfinished = await turn_end_after(tokyo, "What time zone is Tokyo", QuestionEndsTurn())
    ok(unfinished is not None and unfinished > 2.5, f"without a question mark the wait stays: {unfinished and round(unfinished, 2)} s")
    done = await turn_end_after(spoken("How many legs does a spider have?"), "How many legs does a spider have?", QuestionEndsTurn())
    ok(done is not None and done < 1.0, f"a question smart-turn already hears as finished is unchanged: {done and round(done, 2)} s")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
