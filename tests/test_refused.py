"""A request the session refused after you stopped it yourself, by voice (asks Haiku): voice
tells you unprompted that it didn't go in, that it waits in the prompt box for your Enter, and that stopping by
voice instead of Esc avoids this; it sends nothing again and doesn't read the request back. When the box couldn't
take it either, it says you need to type or send something first."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import talker
from talker import FEED, heard_from, make_options

STOPPED = "the session was stopped and nobody has sent input since"
ASK = "Please count to one thousand out loud."
results = []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


async def turn(client, text=""):
    buf, out = io.StringIO(), []
    with redirect_stdout(buf):
        await client.query(talker.turn(FEED.drain(), text))
        async for m in client.receive_response():
            if isinstance(m, AssistantMessage):
                out.append(m)
    sent = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ") and '"submit"' in l]
    return heard_from(out), sent


def fresh():
    FEED.pending, FEED.queued, FEED.busy, FEED.elsewhere = [], [], False, []
    FEED.add({"type": "switched", "initial": True, "session": "aaaa1111-0000-0000-0000-000000000001",
              "title": "Counting", "project": "dev"})


async def main():
    # Offline: the refusal is news worth telling unprompted, and the talker hears what to say.
    fresh()
    worth = FEED.add({"type": "submit_refused", "text": ASK, "why": STOPPED, "inBox": True})
    note = " ".join(FEED.pending).lower()
    ok(worth and "prompt box" in note and "esc" in note, "a refusal after a stop is announced, with the prompt box and Esc")

    fresh()
    async with ClaudeSDKClient(options=make_options()) as c:
        FEED.add({"type": "submit_refused", "text": ASK, "why": STOPPED, "inBox": True})
        said, sent = await turn(c)
        low = said.lower()
        ok("box" in low and "enter" in low, "it says the request is in the prompt box, for an Enter", said)
        ok("voice" in low or "esc" in low or "asking me" in low or "ask me" in low,
           "it says stopping by voice (not Esc) avoids this", said)
        ok(not sent and "thousand" not in low, "nothing sent again, the request not read back", said)

    fresh()
    async with ClaudeSDKClient(options=make_options()) as c:
        FEED.add({"type": "submit_refused", "text": ASK, "why": STOPPED, "inBox": False})
        said, sent = await turn(c)
        low = said.lower()
        ok(("type" in low or "send" in low) and not sent, "the box couldn't take it: it says to type or send something", said)

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
