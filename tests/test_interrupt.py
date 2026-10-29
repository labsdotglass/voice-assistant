"""Interrupting with your words sends them once (no model): the queued copy of the same words is taken back."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

import talker
from talker import FEED, interrupt_session

results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


async def interrupt(then: str) -> list[dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        talker.FEED.drain()  # each interrupt its own reply
        await interrupt_session.handler({"ack": "", "then": then, "confirmed": True})
    return [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]


async def main():
    talker.OUTBOX.clients = {}
    FEED.busy = True
    said = "I changed my mind, let's do the onboarding without the voice. If it turns out we need voice, ask me first."
    FEED.queued = [
        {"id": "q1", "text": "Update the README."},
        {"id": "q2", "text": "I changed my mind, let's do the onboarding without the voice."},  # heard before you finished
        {"id": "q3", "text": said},
    ]
    cmds = await interrupt(said)
    kinds = [c["type"] for c in cmds]
    cancel = next((c for c in cmds if c["type"] == "cancel"), None)
    ok(cancel is not None and sorted(cancel["ids"]) == ["q2", "q3"] and cancel.get("replaced"),
       f"the queued copies of what the interrupt sends are taken back: {cancel}")
    ok(kinds.index("cancel") < kinds.index("interrupt"), "taken back before the interrupt, so the stopped turn can't start them")
    ok([q["id"] for q in FEED.queued] == ["q1"], "other waiting prompts stay")
    ok(next(c for c in cmds if c["type"] == "interrupt")["then"] == said, "the interrupt carries your words, once")

    FEED.queued = [{"id": "q4", "text": "Run the tests."}]
    cmds = await interrupt("Stop that and fix the orb instead.")
    ok(not any(c["type"] == "cancel" for c in cmds) and len(FEED.queued) == 1, "unrelated waiting prompts aren't touched")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
