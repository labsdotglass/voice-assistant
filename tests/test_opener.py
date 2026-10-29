"""Replies open with a short first sentence, so speech starts sooner (asks Haiku)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import re
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, TextBlock

import talker
from talker import FEED, heard_from, make_options

DONE = {"type": "turn_complete", "durationMs": 40000, "isAborted": False,
        "answer": "I moved the badge onto the orb's top-right edge, rebuilt the helper, restarted voice, and queued two test tasks so you can check it."}
CASES = [([DONE], ""), ([DONE], "What did it just do?"), ([], "How are you doing today?"), ([], "Ask it to run the tests.")]


async def main():
    results = []
    for events, said in CASES:
        FEED.pending, FEED.queued, FEED.busy = [], [], False
        for ev in events:
            FEED.add(ev)
        out = []
        async with ClaudeSDKClient(options=make_options()) as client:
            with redirect_stdout(io.StringIO()):
                await client.query(talker.turn(FEED.drain(), said))
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        out.append(msg)  # all of the reply: heard_from speaks it as the live path does
        reply = heard_from(out)
        first = re.split(r"(?<=[.!?])\s", reply)[0]
        n = len(re.findall(r"[\w']+", first))
        ok = 0 < n <= 10
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {said or '(session finished)'!r}: first sentence {n} words\n        heard: {reply!r}")
    # A soft rule the model follows most of the time: one long opener in four is tolerated,
    # more means the rule stopped working.
    print(f"{sum(results)}/{len(results)} passed (at least {len(results) - 1} needed)")
    sys.exit(0 if sum(results) >= len(results) - 1 else 1)


asyncio.run(main())
