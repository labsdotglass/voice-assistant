"""A dead mic (no models): the helper's "mic: dead" and "mic: back" lines reach the daemon, and voice says
it can't hear you once per dead spell, at most once a minute, and not at all if the mic is back first."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import sys
import time
from contextlib import redirect_stdout

import talker
from helper_transport import AudioHelper
from talker import FEED, MIC_DEAD_NOTE

results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + msg)


def fresh():
    FEED.pending.clear()
    FEED.mic_dead = False
    FEED.mic_warned = float("-inf")


class FakeProc:
    """The helper's stderr, line by line, then gone (a plain exit, not a device change)."""

    def __init__(self, lines):
        self._lines = [l.encode() + b"\n" for l in lines]
        self.returncode = 0

    @property
    def stderr(self):
        async def gen():
            for l in self._lines:
                yield l
        return gen()

    async def wait(self):
        return 0


async def main():
    # The helper's lines reach on_mic.
    helper = AudioHelper(24000)
    seen = []
    helper.on_mic = lambda dead, why: seen.append((dead, why))
    proc = FakeProc(["audio_helper: mic: dead (no audio)", "audio_helper: mic: back", "audio_helper: mic: dead (silence)"])
    with redirect_stdout(io.StringIO()):
        await helper._log_stderr(proc)
    ok(seen == [(True, "no audio"), (False, ""), (True, "silence")], f"the helper's mic lines reach the daemon ({seen})")

    # Said once per dead spell.
    fresh()
    ok(FEED.add({"type": "mic_dead", "why": "silence"}) and MIC_DEAD_NOTE in FEED.pending, "the mic goes dead: voice says so")
    FEED.pending.clear()
    ok(not FEED.add({"type": "mic_dead", "why": "no audio"}) and not FEED.pending, "dead again in the same spell: not said twice")

    # A mic that comes back and drops out again within a minute: said once.
    ok(not FEED.add({"type": "mic_back"}), "back: nothing to say")
    ok(not FEED.add({"type": "mic_dead", "why": "silence"}), "dead again within a minute: not said again")
    FEED.add({"type": "mic_back"})
    FEED.mic_warned = time.monotonic() - talker.MIC_WARN_GAP - 1
    ok(FEED.add({"type": "mic_dead", "why": "silence"}), "dead again after a minute: said again")

    # Back before voice could say it (mid-reply, or before the greeting): the warning is gone.
    fresh()
    FEED.add({"type": "mic_dead", "why": "silence"})
    FEED.add({"type": "mic_back"})
    ok(MIC_DEAD_NOTE not in FEED.pending, "the mic is back before the warning was said: it's dropped")

    # The note asks for the warning without giving words to copy.
    ok('"' not in MIC_DEAD_NOTE and "one short sentence" in MIC_DEAD_NOTE, "the note describes the warning, quoting no phrase")


asyncio.run(main())
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
