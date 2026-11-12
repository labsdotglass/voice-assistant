"""Speech voice dropped (no models): each drop takes the orb out of thinking at once, and only what was likely
you gets the cue (the orb's ring and a sound), at most once per cooldown."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import sys
import time
from contextlib import redirect_stdout

from pipecat.frames.frames import TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

import dropped
import talker
from dropped import DROPS
from helper_transport import HallucinationFilter
from speaker import SpeakerGate, Verdict

results = []
cues = []
DROPS.on_cue = lambda: cues.append(True)


def ok(cond, msg):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + msg)


def fresh():
    DROPS._last_cue = float("-inf")
    cues.clear()


def verdict(is_you: bool, score=None, matched=False) -> Verdict:
    v = Verdict()
    v.is_you, v.score, v.matched = is_you, score, matched
    return v


async def through(proc, text: str) -> list:
    out = []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        out.append(frame)

    proc.push_frame = push
    with redirect_stdout(io.StringIO()):
        await proc.process_frame(TranscriptionFrame(text, "u", "t"), FrameDirection.DOWNSTREAM)
    return out


async def main():
    # The rule, straight.
    for reason, text, is_you, score, want, what in [
        ("not you", "Yeah, that's good.", False, 0.30, True, "another voice just under the cutoff: likely you, cue"),
        ("not you", "Previously on the show", False, 0.15, False, "another voice well under it (the TV): no cue"),
        ("silence", "Yes.", True, None, True, "too short to judge, rated as silence: cue"),
        ("hallucination", "Thank you.", True, None, True, "a short thanks the filter dropped: cue"),
        ("hallucination", "Um.", True, None, False, "a filler sound: no cue"),
        ("silence", "and", False, 0.10, False, "rated as silence and not you: no cue"),
    ]:
        fresh()
        ok(DROPS.report(reason, text, is_you, score) == want and bool(cues) == want, what)

    fresh()
    first, second = DROPS.report("not you", "Yes.", False, 0.29), DROPS.report("not you", "Yes yes.", False, 0.30)
    ok(first and not second and len(cues) == 1, "two near misses in a row: one cue, not two")
    DROPS._last_cue = time.monotonic() - dropped.COOLDOWN - 0.1
    ok(DROPS.report("not you", "Yes.", False, 0.29), "after the cooldown: a cue again")

    # The drop points report.
    fresh()
    out = await through(SpeakerGate(verdict(False, 0.31)), "Yes.")
    ok(not out and cues, "the voice check drops a near miss: not answered, and the cue")
    fresh()
    out = await through(SpeakerGate(verdict(False, 0.12)), "Next on the news")
    ok(not out and not cues, "it drops the TV: not answered, no cue")
    fresh()
    out = await through(SpeakerGate(verdict(True, 0.6, matched=True)), "Merge it.")
    ok(len(out) == 1 and not cues, "your voice goes through: no drop")
    fresh()
    out = await through(HallucinationFilter(verdict(True)), "Thanks.")
    ok(not out and cues, "the filter drops a short thanks from you (unchecked): the cue")

    # The orb leaves thinking at once.
    reporter = talker.VoiceStateReporter()
    shown = []
    reporter._on_change = shown.append
    talker.SPEECH_READY.set()
    with redirect_stdout(io.StringIO()):
        reporter._set("thinking")
        DROPS.report("not you", "Next on the news", False, 0.1)
    ok(shown[-1:] == ["listening"], f"any drop: thinking goes back to listening at once ({shown})")
    reporter._set("speaking")
    DROPS.report("not you", "Next on the news", False, 0.1)
    ok(shown[-1:] == ["speaking"], "while voice speaks, a drop changes nothing")


asyncio.run(main())
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
