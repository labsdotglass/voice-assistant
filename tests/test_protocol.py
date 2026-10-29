"""Scripted check of the daemon's focus-following protocol, without audio or models."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import aiohttp

import daemon
import focus
import talker as talker_mod
from talker import FEED, OUTBOX, emit

SOCK = str(Path(tempfile.mkdtemp()) / "h.sock")  # a short temp path: sockets have a 104-byte limit
daemon.OWNER_FILE = Path(SOCK + ".owner")
APP = Path(tempfile.mkdtemp())  # a fake claude-code-sessions folder
focus.SESSIONS = APP
results = []


def ok(c, m):
    results.append(c)
    print(("PASS " if c else "FAIL ") + m)


def focus_on(session, title, at):
    d = APP / "acct" / "org"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"local_{session}.json"
    f.write_text(json.dumps({"cliSessionId": session, "title": title, "lastFocusedAt": at}))
    os.utime(f, (at, at))  # a fresh mtime, as a real write gives


class StubTalker:
    def __init__(self):
        self.events = []

    def on_session_event(self, ev):
        self.events.append(ev)
        FEED.add(ev)


async def main():
    focus_on("A", "Project overview", 100)
    focus_on("B", "Atlas work", 50)
    talker, stopped = StubTalker(), []
    # FocusWatcher picks up the patched SESSIONS through its default argument only at import, so patch the class too.
    focus.FocusWatcher.__init__.__defaults__ = (APP, 0.05)
    runner = await daemon.serve_events(talker, SOCK, on_stop=lambda: stopped.append(True))
    s = aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=SOCK))
    u = "http://voice"
    poll = lambda c, w=15: s.get(f"{u}/commands", params={"client": c, "wait": str(w)})

    ok(OUTBOX.target == "A" and OUTBOX.title == "Project overview", "starts on the session focused last")
    note = FEED.drain()
    ok("Project overview" in note and "A)" in note and "switched" not in note, f"at startup the talker hears the session's name and ID: {note!r}")

    buf = io.StringIO()
    with redirect_stdout(buf):
        emit({"type": "status", "text": "listening"})
    ok("@@VOICE" in buf.getvalue(), "before any session registers, commands go to stdout")

    await s.post(f"{u}/register", json={"session": "B", "client": "B:1"})
    ok(not FEED.busy, "before the focused session checks in, it reads as idle")
    r = await (await s.post(f"{u}/register", json={"session": "A", "client": "A:1", "busy": True})).json()
    ok(FEED.busy and "working on something" in FEED.drain(), "the focused session checks in mid-turn: voice knows it's busy at once")
    emit({"type": "submit", "text": "hi"})
    r = await (await poll("A:1")).json()
    ok(r["owner"] and r["commands"] == [{"type": "submit", "text": "hi"}], f"focused A gets the command: {r}")
    ok(r["title"] == "Project overview" and r["reachable"], "poll carries the title for the panel")
    r = await (await poll("B:1", 0.1)).json()
    ok(r["owner"] is False and r["title"] == "Project overview", "unfocused B gets nothing, but learns the target")

    await s.post(f"{u}/event", json={"type": "turn_start", "text": "x", "session": "B"})
    ok(not any(e.get("type") == "turn_start" for e in talker.events), "B's events don't reach the talker")
    await s.post(f"{u}/event", json={"type": "tool", "tool": "Read", "session": "A"})
    ok(talker.events[-1]["tool"] == "Read", "A's events do")
    FEED.drain()

    wait_b = asyncio.create_task(poll("B:1"))
    await asyncio.sleep(0.1)
    focus_on("B", "Atlas work", 200)  # you click B in the sidebar
    r = await asyncio.wait_for((await wait_b).json(), 2)
    ok(r["owner"] is True, "B's waiting poll wakes up as the owner once you focus it")
    ok(talker.events[-1]["type"] == "switched" and FEED.busy, "talker hears the switch, and that B is busy")
    note = FEED.drain()
    ok("switched" in note and "Atlas work" in note and "B)" in note, f"the switch note gives the new name and ID: {note!r}")
    ok(open(daemon.OWNER_FILE).read() == "B", "owner file follows focus")

    emit({"type": "transcript", "who": "talker", "text": "x"})
    r = await (await poll("B:1")).json()
    ok(r["commands"][0]["text"] == "x", "commands now go to B")

    focus_on("C", "Old session", 300)  # a session still on the old mod: never registers
    await asyncio.sleep(0.2)
    out = await talker_mod.send_to_session.handler({"prompt": "do it"})
    ok("Not sent" in out["content"][0]["text"] and not OUTBOX.queue, "nothing is sent to a session that can't hear voice")
    r = await (await poll("B:1", 0.1)).json()
    ok(r["reachable"] is False and r["title"] == "Old session", "panels say the focused session can't hear voice")

    await s.post(f"{u}/register", json={"session": "C", "client": "C:1"})  # you reopened it: it registered
    talker_mod.FEED.drain()  # a new reply: the same words may be sent again
    out = await talker_mod.send_to_session.handler({"prompt": "do it"})
    r = await (await poll("C:1")).json()
    ok(r["commands"][0]["type"] == "submit" and r["commands"][0]["text"] == "do it", "once it registers, it gets your words")

    r = await s.get(f"{u}/commands", params={"client": "-", "wait": "0"})
    ok(r.ok and (await r.json())["owner"] is False, "the liveness probe answers at once")

    wait_a = asyncio.create_task(poll("A:1"))
    await asyncio.sleep(0.05)
    await s.post(f"{u}/stop")
    r = await asyncio.wait_for((await wait_a).json(), 2)
    ok(r["stopping"] is True, "stop tells every session's poll, so none restarts voice")
    await asyncio.sleep(0.5)
    ok(stopped == [True], "and the daemon stops")

    await s.close()
    await runner.cleanup()
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
