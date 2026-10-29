"""Renaming a session by voice (asks Haiku): voice passes it on to the session as one clear
request carrying the new name, never opens or switches sessions for it, and never says it can't."""

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

results, opened = [], []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


async def turn(client, text):
    buf, out = io.StringIO(), []
    with redirect_stdout(buf):
        await client.query(talker.turn(FEED.drain(), text))
        async for m in client.receive_response():
            if isinstance(m, AssistantMessage):
                out.append(m)
    sent = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ") and '"submit"' in l]
    return heard_from(out), sent


async def main():
    talker.NEW_SESSION["start"] = lambda message, folder, name: opened.append(("new", name))
    talker.NEW_SESSION["go"] = lambda name, project, session_id="": opened.append(("go", name))
    for said_aloud, name in [("Rename this session to test three.", "test three"),
                             ("Can you call this session orb polish?", "orb polish"),
                             ("Change the name of the website setup session to launch prep.", "launch prep")]:
        FEED.pending, FEED.queued, FEED.busy = ["[the session you're working with: \"Project overview\" in the voice-assistant project.]"], [], False
        opened.clear()
        async with ClaudeSDKClient(options=make_options()) as c:
            said, sent = await turn(c, said_aloud)
        text = sent[0]["text"].lower() if sent else ""
        ok(len(sent) == 1 and "name" in text and name in text and not opened,
           f"{said_aloud!r} goes to the session once, with the new name ({text!r}), nothing opened", said)
        ok("can't" not in said.lower() and "cannot" not in said.lower(), "it never says it can't rename", said)
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
