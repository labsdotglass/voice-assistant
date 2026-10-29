"""Voice catches up when it starts or comes to a session. Offline: when the user last wrote and when the
session last replied, read from its transcript (the app's notes and tools' results aren't the user's words);
how long ago, said; voice's own last conversation there as background; a plan from an idle session said as its
last one. By voice (asks Haiku): a greeting mentions what happened here under an hour ago in a short clause and
only greets when it's old; asked where things are, it says how long ago."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import re
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import new_session
import talker
from talker import FEED, ago, greeting_note, heard_from, make_options, when_said

talker.GREETINGS = Path(tempfile.mkdtemp()) / "greetings.json"
SID = "aaaa1111-0000-0000-0000-000000000001"
results = []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


def iso(secs_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - secs_ago))


def fresh():
    FEED.pending, FEED.queued, FEED.forms, FEED.busy, FEED.caught_up = [], [], {}, False, False
    talker.RESTART.update(why="", language="", restored=False)
    FEED.add({"type": "switched", "initial": True, "session": SID, "title": "Docs pass", "project": "voice-assistant"})


def offline():
    root = Path(tempfile.mkdtemp())
    (root / "proj").mkdir()
    rows = [
        {"type": "user", "timestamp": iso(3600), "message": {"content": "Review the README's setup steps."}},
        {"type": "assistant", "timestamp": iso(3500), "message": {"content": [{"type": "text", "text": "Done: two steps fixed."}]}},
        {"type": "user", "timestamp": iso(600), "message": {"content": [{"type": "text", "text": "<system-reminder>a note</system-reminder>\nAnd the Updating section."}]}},
        {"type": "assistant", "timestamp": iso(300), "message": {"content": [{"type": "text", "text": "Updating is fixed too."}, {"type": "tool_use", "name": "Edit"}]}},
        {"type": "user", "timestamp": iso(290), "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "user", "timestamp": iso(280), "message": {"content": "<system-reminder>only the app's note</system-reminder>"}},
    ]
    (root / "proj" / f"{SID}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    got = new_session.last_said(SID, root=root)
    ok(got["user"] and abs(time.time() - 600 - got["user"]) < 5, "your last words: the message with text of its own, not a tool's result or a bare app note")
    ok(got["reply"] and abs(time.time() - 300 - got["reply"]) < 5, "its last reply: the last assistant text")
    ok(new_session.last_said("no-such-session", root=root) == {"user": None, "reply": None}, "no transcript: no times")
    ok(ago(time.time() - 3700) == "an hour ago" and ago(time.time() - 90) == "2 minutes ago" and ago(time.time() - 5) == "just now",
       "how long ago, as said")
    line = when_said({"user": time.time() - 600, "reply": time.time() - 300})
    ok("10 minutes ago" in line and "5 minutes ago" in line, f"both times go to the talker ({line[:90]}...)")

    fresh()
    talker.app_sessions.last_said = lambda sid: {"user": None, "reply": None}
    old = (time.time() - 3 * 86400) * 1000
    FEED.add({"type": "history", "messages": [], "voice": [{"who": "you", "text": "Check the README.", "at": old}]})
    note = "\n".join(FEED.pending)
    ok("last voice conversation" in note and "3 days ago" in note and FEED.caught_up, "voice's own last conversation here comes back as background, with how long ago")
    fresh()
    FEED.add({"type": "plan", "arrived": True, "busy": False, "updates": [{"items": [
        {"content": "Fix the README", "status": "completed"}, {"content": "Ship it", "status": "pending"}]}]})
    ok("isn't working on anything now" in "\n".join(FEED.pending), "a plan an idle session stopped partway through is said as its last one")


async def greet(history: dict, times: dict) -> str:
    fresh()
    talker.app_sessions.last_said = lambda sid: times
    FEED.add({"type": "history", **history})
    FEED.pending.append(greeting_note())
    out = []
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(io.StringIO()):
            await client.query(talker.turn(FEED.drain()))
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    out.append(msg)
    return heard_from(out)


async def live():
    now = time.time()
    recent = {"messages": [{"role": "user", "text": "Review the README's setup steps."},
                           {"role": "assistant", "text": "I fixed two setup steps in the README; the Updating section still needs a pass."}],
              "voice": [{"who": "you", "text": "Ask it to review the README's setup steps.", "at": (now - 1500) * 1000},
                        {"who": "talker", "text": "Sending that over.", "at": (now - 1495) * 1000}]}
    said = await greet(recent, {"user": now - 1500, "reply": now - 1200})
    ok("readme" in said.lower() and len(said.split()) <= 40, "a greeting after recent work here mentions it in a short clause", said)

    stale = {"messages": recent["messages"], "voice": [{**l, "at": l["at"] - 3 * 86400 * 1000} for l in recent["voice"]]}
    said = await greet(stale, {"user": now - 3 * 86400, "reply": now - 3 * 86400 + 300})
    ok("readme" not in said.lower(), "after days away, it only greets", said)

    fresh()
    talker.app_sessions.last_said = lambda sid: {"user": now - 1500, "reply": now - 1200}
    FEED.add({"type": "history", **recent})
    out = []
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(io.StringIO()):
            await client.query(talker.turn(FEED.drain(), "Where are we with this session?"))
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    out.append(msg)
    said = heard_from(out)
    ok(bool(re.search(r"minute|half an hour|ago|earlier", said.lower())) and "readme" in said.lower(),
       "asked where things are: what it did, and how long ago", said)


async def main():
    offline()
    await live()
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
