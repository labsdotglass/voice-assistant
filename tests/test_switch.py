"""Switching sessions starts voice afresh: nothing from the previous session carries over,
neither its conversation (asks Haiku) nor its waiting prompts, questions or plan."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import talker
from talker import FEED, PLAN, heard_from, make_options

results = []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


def switched(initial=False):
    return {"type": "switched", "session": "s2", "title": "Fix the login page", "project": "web-app", "busy": False, "initial": initial}


async def ask(client, text):
    out = []
    with redirect_stdout(io.StringIO()):
        await client.query(talker.turn(FEED.drain(), text))
        async for m in client.receive_response():
            if isinstance(m, AssistantMessage):
                out.append(m)
    return heard_from(out)


async def main():
    # The feed: the previous session's state is dropped.
    FEED.pending = ["[session started on: Refactor the orb.]"]
    FEED.queued = [{"id": "q1", "text": "Update the README."}]
    FEED.forms, FEED.buttons = {"f1": [{"question": "Which way?", "options": ["A", "B"]}]}, {"b1": [{"label": "Yes", "message": "Yes."}]}
    PLAN.steps = [{"content": "Read the code", "status": "in_progress"}]
    FEED.add(switched())
    ok(not FEED.queued and not FEED.forms and not FEED.buttons and not PLAN.steps, "the previous session's waiting prompts, questions and plan are gone")
    ok(len(FEED.pending) == 1 and "Fix the login page" in FEED.pending[0], f"only the new session is in the feed: {FEED.pending}")

    # The conversation: replaced by a fresh one, the old one closed.
    class Fake:
        made, closed = [], []

        def __init__(self, options=None):
            Fake.made.append(self)

        async def connect(self): pass

        async def disconnect(self): Fake.closed.append(self)

    real = talker.ClaudeSDKClient
    talker.ClaudeSDKClient = Fake
    t = talker.Talker()
    t.create_task = asyncio.create_task
    first = t._client = Fake()
    await t._start_over()
    await asyncio.sleep(0)
    talker.ClaudeSDKClient = real
    ok(t._client is not first and first in Fake.closed, "a switch replaces the talker's conversation and closes the old one")

    # Through Haiku: what was said before the switch is forgotten after it.
    FEED.pending, FEED.queued, FEED.busy = ["[the session you're working with: \"Refactor the orb\" in the voice-assistant project.]"], [], False
    async with ClaudeSDKClient(options=make_options()) as before:
        await ask(before, "Just so you know, today's code word is pineapple. No need to send that anywhere.")
    FEED.add(switched())
    async with ClaudeSDKClient(options=make_options()) as after:  # what _start_over makes
        said = await ask(after, "What's today's code word?")
    ok("pineapple" not in said.lower(), "after a switch, it doesn't know what was said before", said)

    # The new session's own history arrives from its mod: background to answer from, never announced.
    FEED.pending, FEED.queued, FEED.busy = [], [], False
    FEED.add(switched())
    history = {"type": "history", "messages": [
        {"role": "user", "text": "The login button is hard to see. Make it green and a bit bigger."},
        {"role": "assistant", "text": "Done: the login button is now green (#2e7d32) and 20% larger. Tests pass; not committed yet."},
        {"role": "user", "text": "Also check it on mobile."},
        {"role": "assistant", "text": "On mobile it wrapped onto two lines; I shortened the label to 'Log in'. Still not committed."},
    ]}
    ok(not FEED.add(history), "a session's history isn't announced on its own")
    async with ClaudeSDKClient(options=make_options()) as fresh:
        buf = io.StringIO()
        out = []
        with redirect_stdout(buf):
            await fresh.query(FEED.drain() + "What have we been doing in this session so far?")
            async for m in fresh.receive_response():
                if isinstance(m, AssistantMessage):
                    out.append(m)
        said = heard_from(out)
    sent = [l for l in buf.getvalue().splitlines() if '"submit"' in l]
    low = said.lower()
    ok(not sent and "login" in low.replace("log in", "login") and ("green" in low or "mobile" in low or "commit" in low),
       "after a switch it answers from that session's own history, without asking the session", said)

    # Arriving in a session that waits for the user's answer: an open form, or its closing card.
    FORM = [{"question": "Which login button color?", "options": ["Green", "Blue"], "multiSelect": False}]
    CARD = {"type": "question", "id": "c1", "arrived": True,
            "questions": [{"question": "Commit the login fix?", "options": ["Commit it", "Not yet"], "multiSelect": False}],
            "buttons": [{"label": "Commit it", "message": "Commit the login fix."}, {"label": "Not yet", "message": "Not yet."}]}
    for arrival, answer, want in [({"type": "question", "id": "f1", "questions": FORM, "arrived": True}, "Green.", "answer"),
                                  (CARD, "Commit it.", "submit")]:
        FEED.pending, FEED.queued, FEED.forms, FEED.buttons, FEED.busy = [], [], {}, {}, False
        FEED.add(switched())
        ok(FEED.add(arrival), "arriving in a session that waits for an answer is announced")
        async with ClaudeSDKClient(options=make_options()) as c:
            buf, out = io.StringIO(), []
            with redirect_stdout(buf):
                await c.query(talker.turn(FEED.drain()))
                async for m in c.receive_response():
                    if isinstance(m, AssistantMessage):
                        out.append(m)
            said = heard_from(out)
            low = said.lower()
            ok(said.rstrip().endswith("?") and ("green" in low or "commit" in low) and "idle" not in low,
               "voice asks the waiting question straight away", said)
            buf = io.StringIO()
            with redirect_stdout(buf):
                await c.query(talker.turn(FEED.drain(), answer))
                async for m in c.receive_response():
                    pass
        cmds = [json.loads(l[8:])["type"] for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
        ok(want in cmds, f"answering out loud works there ({cmds})")

    # A permission request on screen: the session waits for the user, who answers it there, not by voice.
    FEED.pending, FEED.queued, FEED.forms, FEED.buttons, FEED.busy = [], [], {}, {}, True
    FEED.add(switched())
    ok(FEED.add({"type": "permission", "id": "t9", "what": "edit register.tsx", "arrived": True}), "arriving at a waiting permission request is announced")
    async with ClaudeSDKClient(options=make_options()) as c:
        await ask(c, "")
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = []
            await c.query(FEED.drain() + "Is the session waiting for me?")
            async for m in c.receive_response():
                if isinstance(m, AssistantMessage):
                    out.append(m)
        said = heard_from(out)
        low = said.lower()
        ok(low.startswith("yes") or "waiting" in low and "register" in low, "asked if it's waiting: yes, for permission to edit register.tsx", said)
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = []
            await c.query(FEED.drain() + "Okay, allow it.")
            async for m in c.receive_response():
                if isinstance(m, AssistantMessage):
                    out.append(m)
        said = heard_from(out)
        sent = [l for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ") and ('"submit"' in l or '"answer"' in l)]
        ok(not sent and "screen" in said.lower(), "asked to allow it, voice sends nothing and points to the screen", said)
    FEED.add({"type": "permission_done", "id": "t9"})
    ok(not FEED.permissions, "allowed on screen: the request is gone")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
