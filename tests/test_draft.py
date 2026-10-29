"""The draft bubble grows across the pieces of your turn instead of starting over (no models)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from pipecat.frames.frames import TranscriptionFrame
from pipecat.tests.utils import run_test

from interim import SaidSoFar
from talker import SAID

results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + msg)


async def main():
    buf = io.StringIO()
    with redirect_stdout(buf):  # the mod's commands
        await run_test(SaidSoFar(), frames_to_send=[
            TranscriptionFrame("Let's check again,", "", ""),
            TranscriptionFrame("can we make it smoother?", "", ""),
        ])
    drafts = [json.loads(l[8:])["text"] for l in buf.getvalue().splitlines() if '"draft"' in l]
    ok(drafts == ["Let's check again,", "Let's check again, can we make it smoother?"], f"each finished piece is kept: {drafts}")
    ok(SAID == ["Let's check again,", "can we make it smoother?"], "so the next piece's draft starts after them")
    SAID.clear()  # what the talker does when your whole turn goes out
    ok(not SAID, "cleared once the turn is sent")
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
