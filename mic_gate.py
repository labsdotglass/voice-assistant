"""Voice ignores the mic until it's ready (card #38).

While voice loads (the orb's spinner, until the ready tick), the mic is already on. What you said then used to be
acted on half-loaded, or lost. MicGate sits right after the transport's input and drops its audio until voice is
ready (VOICE_READY: the orb's first state, talker.VoiceStateReporter), so nothing said before the tick reaches the
voice activity detection, Whisper or the voice check, and nothing is answered later. Learning your voice (the
first run's Your voice step, Learn my voice again) needs the mic before that, so it passes while ENROLLING is on.

Talking across the moment it becomes ready drops that whole turn: half a sentence is worse than none. Once ready,
the gate opens at the first quiet stretch (QUIET_SECS below QUIET_RMS), or after MAX_WAIT_SECS whatever the room.
A restart (a language switch re-executes the daemon) starts with a fresh gate, so it's held the same way.
"""

import time
from typing import Callable

import numpy as np
from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

QUIET_RMS = 300  # int16 level under which a frame counts as quiet (about -40 dBFS)
QUIET_SECS = 0.3  # how long it has to stay quiet once voice is ready before the mic opens
MAX_WAIT_SECS = 1.5  # after ready, the mic opens by then however loud the room is


def rms(audio: bytes) -> float:
    pcm = np.frombuffer(audio, dtype=np.int16)
    return float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) if pcm.size else 0.0


class MicGate(FrameProcessor):
    def __init__(self, ready: Callable[[], bool], enrolling: Callable[[], bool], clock: Callable[[], float] = time.monotonic, **kwargs):
        super().__init__(**kwargs)
        self._ready = ready
        self._enrolling = enrolling
        self._clock = clock
        self._open = False
        self._ready_at: float | None = None  # when voice became ready, while the gate waits for a quiet stretch
        self._quiet = 0.0  # seconds of quiet in a row since then
        self._dropped = 0.0  # seconds of audio dropped
        self._loud = 0.0  # of which loud: likely speech

    def _drop(self, frame: InputAudioRawFrame, secs: float, loud: bool):
        self._dropped += secs
        if loud:
            self._loud += secs

    def _opens(self, why: str):
        self._open = True
        logger.info(f"[mic] ignored until ready: {self._dropped:.1f} s of audio, {self._loud:.1f} s of it loud; open {why}")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if self._open or not isinstance(frame, InputAudioRawFrame) or self._enrolling():
            await self.push_frame(frame, direction)
            return
        secs = len(frame.audio) / 2 / max(frame.sample_rate * frame.num_channels, 1)
        loud = rms(frame.audio) >= QUIET_RMS
        if not self._ready():
            self._drop(frame, secs, loud)
            return
        now = self._clock()
        if self._ready_at is None:
            self._ready_at = now
            if not loud and self._loud == 0:  # nobody talking as it becomes ready: open at once
                self._opens("as voice became ready")
                await self.push_frame(frame, direction)
                return
        self._quiet = 0.0 if loud else self._quiet + secs
        if self._quiet >= QUIET_SECS:
            self._opens("after the turn that ran across the ready tick")
        elif now - self._ready_at >= MAX_WAIT_SECS:
            self._opens(f"{MAX_WAIT_SECS} s after ready, the room still loud")
        else:
            self._drop(frame, secs, loud)
            return
        await self.push_frame(frame, direction)
