"""Interrupting the session and clearing the conversation wait for your yes: voice asks
whether you're sure first (asks Haiku), and the tools refuse without it (no model)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import talker
from talker import FEED, MENU, heard_from, interrupt_session, make_options, voice_menu

import tempfile
from pathlib import Path

talker.CHECKS = Path(tempfile.mkdtemp()) / "checks.json"  # not your real history of checks
results = []
cleared = []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


def menu(action, value):
    if action == "clear_conversation":
        cleared.append(action)
    return True, "Done."


async def turn(client, text):
    buf, out = io.StringIO(), []
    with redirect_stdout(buf):
        await client.query(talker.turn(FEED.drain(), text))
        async for m in client.receive_response():
            if isinstance(m, AssistantMessage):
                out.append(m)
    cmds = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
    return heard_from(out), [c["type"] for c in cmds if c["type"] in ("interrupt", "submit")]


async def main():
    MENU["do"] = menu
    talker.OUTBOX.clients = {}

    # No model: without the user's yes, neither runs.
    with redirect_stdout(io.StringIO()) as buf:
        await interrupt_session.handler({"ack": "", "then": "", "confirmed": False})
    ok('"interrupt"' not in buf.getvalue(), "the interrupt tool doesn't stop anything without the user's yes")
    FEED.drain()
    with redirect_stdout(io.StringIO()):
        await voice_menu.handler({"ack": "", "action": "clear_conversation", "value": "", "confirmed": False})
    ok(not cleared, "the menu doesn't clear the conversation without the user's yes")

    def busy():
        FEED.pending, FEED.queued, FEED.forms, FEED.busy = [], [], {}, True
        FEED.add({"type": "turn_start", "text": "Refactor the focus watcher."})

    for first, answer, should in [("Stop what it's doing right now.", "Yes, stop it.", True),
                                  ("Interrupt the session.", "No, actually let it finish.", False)]:
        busy()
        async with ClaudeSDKClient(options=make_options()) as c:
            said, cmds = await turn(c, first)
            ok("interrupt" not in cmds and "?" in said, f"{first!r}: it asks whether you're sure, and stops nothing yet", said)
            said, cmds = await turn(c, answer)
        ok(("interrupt" in cmds) == should, f"{answer!r}: {'stops it' if should else 'leaves it running'} ({cmds})", said)

    for first, answer, should in [("Clear our conversation.", "Yes.", True), ("Clear the conversation.", "No, keep it.", False)]:
        FEED.pending, FEED.queued, FEED.busy = [], [], False
        cleared.clear()
        async with ClaudeSDKClient(options=make_options()) as c:
            said, _ = await turn(c, first)
            ok(not cleared and "?" in said, f"{first!r}: it asks whether you're sure, and clears nothing yet", said)
            said, _ = await turn(c, answer)
        ok(bool(cleared) == should, f"{answer!r}: {'clears it' if should else 'keeps it'} ({cleared})", said)

    # Asked in its own words: not one set question, across conversations or within one.
    import re

    def overlap(a, b):
        wa, wb = set(re.findall(r"[a-z']+", a.lower())), set(re.findall(r"[a-z']+", b.lower()))
        return len(wa & wb) / max(1, min(len(wa), len(wb)))

    # In one conversation, as you'd hear it: three checks in a row, each its own question.
    busy()
    asked = []
    async with ClaudeSDKClient(options=make_options()) as c:
        for request, answer in [("Stop what it's doing.", "No, let it run."), ("Clear our conversation.", "No, keep it."),
                                ("Actually, kill that run.", "No.")]:
            said, cmds = await turn(c, request)
            asked.append(said)
            await turn(c, answer)
    openings = [" ".join(re.findall(r"[a-z']+", a.lower())[:3]) for a in asked]
    ok(all("?" in a for a in asked) and len(set(openings)) == len(openings),
       f"three checks in one conversation, each opening its own way ({openings})", " | ".join(asked))
    busy()
    async with ClaudeSDKClient(options=make_options()) as c:
        first, _ = await turn(c, "Stop it.")
        await turn(c, "No, let it run.")
        await turn(c, "What's it working on, by the way?")
        again, cmds = await turn(c, "Stop it.")
    ok("?" in again and overlap(first, again) < 0.7 and "interrupt" not in cmds,
       f"a new request later in the same conversation: checked again, worded anew (alike: {overlap(first, again):.2f})", f"{first} | {again}")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
