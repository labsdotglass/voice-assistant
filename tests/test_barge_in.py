"""Talking over voice (no models): BargeInTrace logs, for each run of your speech while voice speaks, whether
the VAD heard you, missed you, or heard you only once voice finished, and how long the speaker took to stop."""

import _root  # noqa: F401  (the repo root on the import path)
import sys

from pipecat.audio.vad.vad_analyzer import VADParams

from helper_transport import BargeInTrace

results = []
F = 0.032  # one Silero frame at 16 kHz
NORMAL = VADParams()
STRICT = VADParams(confidence=0.85, start_secs=0.3, stop_secs=NORMAL.stop_secs, min_volume=0.8)
LOUD, SOFT, QUIET = (0.95, 0.9), (0.9, 0.7), (0.1, 0.1)  # (confidence, volume): passes strict, normal only, neither


def ok(cond, msg):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + msg)


def trace(strict=True) -> BargeInTrace:
    b = BargeInTrace()
    b.normal, b.strict = NORMAL, STRICT if strict else None
    return b


def feed(b, frames, now, speaking=False):
    """frames ending at `now`, as one analyzer pass."""
    b.frames(frames, speaking, now, F)
    return now


def kinds(b):
    return [k for k, _ in b.events]


# Heard: loud speech, the VAD says speaking after 10 frames, then the flush.
b = trace()
feed(b, [LOUD] * 10, 10.0, speaking=True)
ok(kinds(b) == ["heard"], f"loud speech over voice is logged as heard: {kinds(b)}")
_, info = b.events[0]
ok(info["passed"] == 10 and info["frames"] == 10 and abs(info["secs"] - 9 * F) < 0.01, f"with its frames and time: {info}")
b.flushed(10.12)
ok(kinds(b)[-1] == "flushed" and b.events[-1][1]["secs"] == 0.12, f"and the speaker's stop, timed from it: {b.events[-1]}")
b.flushed(11.0)
ok(len(b.events) == 2, "a flush with no barge-in before it logs nothing")

# Still speaking: the VAD stays SPEAKING for the rest of the utterance, and that's one "heard", not one per pass.
b = trace()
now = feed(b, [LOUD] * 10, 10.0, speaking=True)
for _ in range(20):
    now = feed(b, [LOUD], now + F, speaking=True)
ok(kinds(b) == ["heard"], f"one utterance is heard once, however long it goes on: {kinds(b)}")
now = feed(b, [QUIET] * 10, now + 10 * F)
feed(b, [LOUD] * 10, now + 10 * F, speaking=True)
ok(kinds(b) == ["heard", "heard"], f"and the next one is heard again: {kinds(b)}")

# Unflushed: heard, but voice spoke on until it finished.
b = trace()
feed(b, [LOUD] * 10, 10.0, speaking=True)
b.strict = None
b.bot_stopped(13.0)
ok(kinds(b) == ["heard", "unflushed"], f"heard with no stop before voice finished is logged: {kinds(b)}")

# Missed: soft speech the strict check never lets through, then a pause.
b = trace()
now = feed(b, [SOFT] * 20, 10.0)
feed(b, [QUIET] * 20, now + 20 * F)
ok(kinds(b) == ["missed"], f"soft speech voice never heard is logged as missed: {kinds(b)}")
_, info = b.events[0]
ok(info["passed"] == 0 and info["frames"] == 20 and info["vol"] == 0.7, f"with what kept it out: {info}")

# A short pause inside a sentence doesn't end the run.
b = trace()
now = feed(b, [SOFT] * 5 + [QUIET] * 5 + [SOFT] * 5, 10.0)
ok(kinds(b) == [], "a pause under half a second keeps it one run")

# Late: voice finishes speaking while you're still talking over it.
b = trace()
feed(b, [SOFT] * 30, 10.0)
b.strict = None
b.bot_stopped(10.2)
ok(kinds(b) == ["late"], f"speech voice heard only after it finished is logged as late: {kinds(b)}")

# Restart: the params set again mid-run.
b = trace()
feed(b, [LOUD] * 5, 10.0)
b.restarted("voice started speaking")
ok(kinds(b) == ["restart"], f"params set again mid-run are logged: {kinds(b)}")
b = trace()
b.restarted("voice started speaking")
ok(kinds(b) == [], "and not when you weren't talking")

# Nothing while voice is quiet: the normal VAD hears you as usual.
b = trace(strict=False)
feed(b, [SOFT] * 30, 10.0)
feed(b, [QUIET] * 30, 11.0)
ok(kinds(b) == [], "nothing is traced while voice isn't speaking")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
