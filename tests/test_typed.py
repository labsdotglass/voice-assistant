"""What you type in the chat yourself reaches voice as yours. Offline: it's background, never announced.
By voice (asks Haiku): asked what you just asked the session, voice answers from what you typed, without asking
the session and without sending it on; asked whether it sees what you type, it says yes."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

from talker import ENDING, FEED, LAST_SENT, heard_from, make_options

TYPED = "Rename the orb menu's Show tour item to Take the tour."
results = []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


def fresh():
    FEED.pending, FEED.queued, FEED.forms, FEED.busy = [], [], {}, False
    LAST_SENT.update(text="", at=0.0)
    ENDING.cancel_quietly()
    FEED.add({"type": "switched", "initial": True, "session": "aaaa1111-0000-0000-0000-000000000001",
              "title": "Orb menu", "project": "voice-assistant"})


async def main():
    fresh()
    worth = FEED.add({"type": "typed", "text": TYPED})
    note = " ".join(FEED.pending)
    ok(not worth and TYPED in note and "typed" in note, "a typed message is filed as yours, not announced")

    fresh()
    FEED.add({"type": "typed", "text": TYPED})
    FEED.add({"type": "turn_start", "text": TYPED})
    buf, out = io.StringIO(), []
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(buf):
            await client.query(FEED.drain() + "What did I just ask it to do?")
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    out.append(msg)
    said = heard_from(out)
    sent = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ") and '"submit"' in l]
    ok("tour" in said.lower() and not sent, "asked what you asked: answered from what you typed, nothing sent", said)

    # Live, 27 Oct: right after answering from a typed message, voice said it couldn't see what you type.
    fresh()
    FEED.add({"type": "typed", "text": "Typed test: the code word is avocado."})
    out = []
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(io.StringIO()):
            await client.query(FEED.drain() + "So you can see the messages I type, right?")
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    out.append(msg)
    said = heard_from(out)
    low = said.lower()
    ok(("yes" in low or "i can" in low or "i see" in low) and "can't see" not in low and "cannot see" not in low,
       "asked whether it sees what you type: yes", said)

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
