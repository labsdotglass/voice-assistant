"""Targeted check: names over IDs, no IDs spoken, full IDs kept in what goes to the session."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, TextBlock

import talker
from talker import FEED, heard_from, make_options, short_ids

SID = "af81c816-5e34-4219-8320-e88d6f4b5dde"
META = ["my instructions", "my rules", "rule", "not allowed", "i'm told", "i was told"]  # talk about its own instructions
fails = 0


async def ask(client, text):
    """What the talker says, before the voice's safety net, and the commands it emits."""
    buf, out = io.StringIO(), []
    with redirect_stdout(buf):
        await client.query(talker.turn(FEED.drain(), text))
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                out.append(msg)  # all of the reply: heard_from speaks it as the live path does
    cmds = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
    return heard_from(out), cmds


def check(name, ok, reply, cmds=()):
    global fails
    ok = ok and not any(w in reply.lower() for w in META)
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n        heard: {reply!r}")
    for c in cmds:
        if c.get("type") == "submit":
            print(f"        -> session: {c['text']!r}")


async def main():
    async with ClaudeSDKClient(options=make_options()) as client:
        FEED.add({"type": "switched", "title": "Project overview", "project": "voice-assistant", "session": SID, "initial": True})
        r, _ = await ask(client, "Which session are you working with?")
        check("names the session, no ID", "project overview" in r.lower() and "af81" not in r, r)
        r, _ = await ask(client, "What's the session ID?")
        check("asked for the ID: none spoken", "af81" not in r and "dde" not in r, r)
        r, _ = await ask(client, "Read me the full session ID, all of it.")
        check("asked for all of it: none spoken", "af81" not in r and "dde" not in r, r)

        FEED.add({"type": "turn_complete", "durationMs": 30000, "isAborted": False,
                  "answer": "Committed the naming change as 3ac8e7f2b91d and pushed it. Task task_9f31c2a7 is still running."})
        r, _ = await ask(client, "What did it just do?")
        check("says what it did by name, no IDs", "naming" in r.lower() and "3ac8" not in r and "9f31" not in r, r)
        r, _ = await ask(client, "What's the commit hash?")
        check("commit hash: none spoken", "3ac8" not in r and "b91d" not in r, r)

        r, cmds = await ask(client, "Ask it to show me the diff of that commit.")
        sent = " ".join(c["text"] for c in cmds if c.get("type") == "submit")
        check("the session gets the full hash", "3ac8e7f2b91d" in sent, r, cmds)

        FEED.add({"type": "switched", "title": "", "project": "payments-api", "session": "7c79e0c0-fa2f-4526-83aa-86912fea89f0"})
        r, _ = await ask(client, "Which session am I in now?")
        check("unnamed session: named by its project, no ID", "payments" in r.lower() and "7c79" not in r, r)
    print(f"{7 - fails}/7")
    sys.exit(1 if fails else 0)


asyncio.run(main())
