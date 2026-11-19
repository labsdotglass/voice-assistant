"""Voice ignores the mic until it's ready (card #38, mic_gate.MicGate), no models: audio before the ready tick is
dropped, not buffered; a turn running across the tick is dropped whole, until a quiet stretch; learning your voice
gets the mic before ready; other frames always pass; and the log says how much was ignored."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio

import numpy as np
from loguru import logger
from pipecat.frames.frames import InputAudioRawFrame, TextFrame
from pipecat.processors.frame_processor import FrameDirection

from mic_gate import MAX_WAIT_SECS, QUIET_SECS, MicGate

RATE = 16000
CHUNK = 0.02  # seconds per frame
results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + msg)


def frame(loud: bool) -> InputAudioRawFrame:
    n = int(RATE * CHUNK)
    pcm = (np.sin(np.arange(n) / 4) * (8000 if loud else 30)).astype(np.int16)
    return InputAudioRawFrame(audio=pcm.tobytes(), sample_rate=RATE, num_channels=1)


class Setup:
    def __init__(self):
        self.ready = False
        self.enrolling = False
        self.now = 0.0
        self.out = []
        self.gate = MicGate(ready=lambda: self.ready, enrolling=lambda: self.enrolling, clock=lambda: self.now)

        async def push(f, direction=FrameDirection.DOWNSTREAM):
            self.out.append(f)

        self.gate.push_frame = push

    async def feed(self, loud: bool, n: int = 1) -> int:
        """n frames in; how many came out."""
        before = len(self.out)
        for _ in range(n):
            await self.gate.process_frame(frame(loud), FrameDirection.DOWNSTREAM)
            self.now += CHUNK
        return len(self.out) - before


async def main():
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), format="{message}")

    s = Setup()
    ok(await s.feed(True, 50) == 0, "talking while voice loads: none of it gets through")
    ok(await s.feed(False, 10) == 0, "quiet while loading: still nothing")
    s.ready = True
    await s.feed(True, 1)
    ok(len(s.out) == 0, "voice becomes ready mid-sentence: that turn is still dropped")
    quiet_frames = int(QUIET_SECS / CHUNK) + 1
    passed = await s.feed(False, quiet_frames)
    ok(passed >= 1, "after a quiet stretch once ready, the mic opens")
    ok(await s.feed(True, 5) == 5, "then everything you say gets through")
    line = next((l for l in lines if "[mic] ignored until ready" in l), "")
    ok("1.0 s of it loud" in line, f"the log says how much was ignored, and how much was loud (51 loud frames): {line!r}")

    s = Setup()
    await s.feed(False, 20)
    s.ready = True
    ok(await s.feed(False, 1) == 1, "nobody talking as voice becomes ready: the mic opens at once")

    s = Setup()
    s.ready = True
    s.gate._loud = 1.0  # spoke while loading
    ok(await s.feed(True, int(MAX_WAIT_SECS / CHUNK) - 2) == 0, "a loud room after ready: still held for a while")
    ok(await s.feed(True, 5) >= 1, f"but the mic opens after {MAX_WAIT_SECS} s however loud the room is")

    s = Setup()
    s.enrolling = True
    ok(await s.feed(True, 3) == 3, "learning your voice before ready (the first run): the mic gets through")
    s.enrolling = False
    ok(await s.feed(True, 3) == 0, "enrollment over, voice still loading: held again")

    s = Setup()
    await s.gate.process_frame(TextFrame("hi"), FrameDirection.DOWNSTREAM)
    ok(len(s.out) == 1, "frames other than mic audio always pass")

    logger.remove(sink)


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
raise SystemExit(0 if all(results) else 1)
