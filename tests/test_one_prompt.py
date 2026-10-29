"""Several tasks said in one go reach the session as one prompt, never split into separate prompts or
queued one by one, whether the session is idle or busy; a single request is still one prompt (asks Haiku)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import talker
from talker import ENDING, FEED, LAST_SENT, heard_from, make_options

results = []

SEVERAL = ("Fix three things: the orb flickers when it starts, the transcript panel scrolls to the top on every new line, "
           "and the version in the README is out of date.")
ONE = "Make the orb a little smaller."


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


async def say(text: str, busy: bool) -> tuple[str, list[dict]]:
    FEED.pending, FEED.queued, FEED.forms, FEED.busy = [], [], {}, busy
    LAST_SENT.update(text="", at=0.0)  # each case is new: the same fixes, said again, aren't a duplicate call
    ENDING.cancel_quietly()
    buf, out = io.StringIO(), []
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(buf):
            await client.query(talker.turn(FEED.drain(), text))
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    out.append(msg)
    cmds = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
    return heard_from(out), [c for c in cmds if c["type"] == "submit"]


async def main():
    for busy in (False, True):
        said, sent = await say(SEVERAL, busy)
        text = sent[0]["text"].lower() if len(sent) == 1 else ""
        all_three = all(w in text for w in ("orb", "transcript", "readme"))
        ok(len(sent) == 1 and all_three,
           f"three fixes in one go, session {'busy' if busy else 'idle'}: one prompt with all three (sent {len(sent)}: {[c['text'][:80] for c in sent]})", said)
    said, sent = await say(ONE, False)
    ok(len(sent) == 1, f"a single request is one prompt (sent {len(sent)})", said)
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
