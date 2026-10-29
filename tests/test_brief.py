"""Replies stay short and easy to follow by ear: a few short sentences at most, not a retelling (asks Haiku)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import re
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import talker
from talker import FEED, heard_from, make_options

# A long, detailed answer: the kind that used to be retold in full.
DONE = {"type": "turn_complete", "durationMs": 95000, "isAborted": False,
        "answer": "I tracked the double send down to the interrupt path: the mod resubmitted the queued prompt after the abort, "
                  "and the daemon also sent it again on reconnect. I removed the resubmit in register.tsx, added a guard in "
                  "daemon.py keyed on the prompt id, wrote test_interrupt.py with three cases, ran all 26 checks (all passed), "
                  "committed it and pushed to main. One caveat: a reconnect during a long tool call still delays the prompt "
                  "by a second or two, which I left as is."}
CASES = [([DONE], ""), ([DONE], "What's the status?"), ([], "How does voice know which session to talk to?")]
MAX_WORDS = 40  # before the brevity rules these ran 45 to 60 words; now about 30
MAX_SENTENCE = 18  # words in the longest sentence


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
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", reply) if s.strip()]
        n = len(re.findall(r"[\w']+", reply))
        longest = max((len(re.findall(r"[\w']+", s)) for s in sentences), default=0)
        ok = 0 < n <= MAX_WORDS and longest <= MAX_SENTENCE
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {said or '(session finished)'!r}: {n} words, longest sentence {longest}\n        heard: {reply!r}")
    # A soft rule the model follows most of the time: one long reply in three is tolerated,
    # more means the rule stopped working.
    print(f"{sum(results)}/{len(results)} passed (at least {len(results) - 1} needed)")
    sys.exit(0 if sum(results) >= len(results) - 1 else 1)


asyncio.run(main())
