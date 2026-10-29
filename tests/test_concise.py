"""Prompts voice sends to the session are short, with nothing the user didn't ask for (asks Haiku)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import re
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import talker
from talker import FEED, make_options

DONE = {"type": "turn_complete", "durationMs": 30000, "isAborted": False,
        "answer": "I fixed the orb's Stop voice and pushed it; voice now stops from the orb."}
# What you say, and words a padded prompt tends to bring in that you never said.
CASES = [
    ("Thanks, and can you also have it check the logs?", ["error", "warning", "especially", "thanks", "fix"]),
    ("Ask it to add a short README.", ["setup", "usage", "section", "install", "example"]),
    ("Have it run the tests.", ["report", "fail", "summar", "all of", "and tell"]),
    ("Tell it to commit and push.", ["message", "describ", "make sure", "first", "check"]),
]
results = []


def words(text):
    return re.findall(r"[\w']+", text.lower())


async def sent(said: str) -> str:
    FEED.pending, FEED.queued, FEED.busy = [], [], False
    FEED.add(DONE)
    buf = io.StringIO()
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(buf):
            await client.query(talker.turn(FEED.drain(), said))
            async for msg in client.receive_response():
                pass
    cmds = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
    return " ".join(c["text"] for c in cmds if c["type"] == "submit")


async def main():
    for said, padding in CASES:
        prompt = await sent(said)
        added = [w for w in padding if w in prompt.lower() and w not in said.lower()]
        short = 0 < len(words(prompt)) <= len(words(said)) + 6
        ok = short and not added
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {said!r}\n        -> session: {prompt!r}" + (f"  (added: {added})" if added else ""))
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
