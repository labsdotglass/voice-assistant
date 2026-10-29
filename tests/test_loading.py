"""The orb stays gray ("starting") until the greeting starts playing, then shows the state voice is in (no models, no audio)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import sys
from contextlib import redirect_stdout

from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame
from pipecat.tests.utils import run_test

from talker import SPEECH_READY, VoiceStateReporter

results = []


def ok(cond, msg):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + msg)


async def states(reporter, frames):
    with redirect_stdout(io.StringIO()):  # the reporter's state lines for the mod
        await run_test(reporter, frames_to_send=frames)


async def main():
    shown: list[str] = []
    ticks: list[int] = []
    tick = lambda: ticks.append(1)
    await states(VoiceStateReporter(on_change=shown.append, gray_until_greeting=True, on_ready=tick), [UserStartedSpeakingFrame(), UserStoppedSpeakingFrame()])
    ok(shown == [] and ticks == [], f"before the greeting plays, the orb stays gray and silent however voice changes state (got {shown})")

    await states(VoiceStateReporter(on_change=shown.append, gray_until_greeting=True, on_ready=tick), [BotStartedSpeakingFrame(), BotStoppedSpeakingFrame()])
    ok(shown == ["speaking", "listening"], f"the greeting starts playing: the orb turns to speaking (got {shown})")
    ok(ticks == [1], f"with a greeting: one tick as the orb turns colored (got {len(ticks)})")

    # A greeting that never plays: once the models are loaded and the wait is over, the orb shows where voice is.
    shown.clear()
    reporter = VoiceStateReporter(on_change=shown.append, gray_until_greeting=True)
    reporter.GREETING_TIMEOUT = 0.2
    await states(reporter, [UserStartedSpeakingFrame()])
    with redirect_stdout(io.StringIO()):
        await asyncio.wait_for(reporter._release_late(), 2)
    ok(shown == ["hearing"], f"no greeting after the wait: the orb leaves gray anyway (got {shown})")


    # No greeting (VOICE_GREETING=0): gray only while the models load, then a tick.
    shown.clear()
    ticks.clear()
    reporter = VoiceStateReporter(on_change=shown.append, on_ready=tick)
    SPEECH_READY.clear()
    await states(reporter, [UserStartedSpeakingFrame()])
    ok(shown == [], f"no greeting: gray while the models load (got {shown})")
    asyncio.get_running_loop().call_later(0.2, SPEECH_READY.set)
    with redirect_stdout(io.StringIO()):
        await asyncio.wait_for(reporter._release_late(), 2)
    ok(shown == ["hearing"], f"no greeting: once loaded, the orb shows where voice is (got {shown})")
    ok(ticks == [1], f"no greeting: one tick as the orb turns colored (got {len(ticks)})")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
