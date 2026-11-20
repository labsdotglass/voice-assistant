"""The orb shows when another session waits on you (card #14), offline: the daemon's flag goes on with a permission
or a question form in a session you're not in, off once it's answered, never for the session you're in (you see its
card there), on again for one you left unanswered, and it reaches the orb's "S" message."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import aiohttp

import daemon
import focus
import new_session
from helper_transport import AudioHelper
from talker import FEED

SOCK = str(Path(tempfile.mkdtemp()) / "w.sock")  # a short temp path: sockets have a 104-byte limit
daemon.OWNER_FILE = Path(SOCK + ".owner")
APP = Path(tempfile.mkdtemp())  # a fake claude-code-sessions folder
focus.SESSIONS = APP
new_session.turn_summary.__defaults__ = (APP,)
new_session.WAITING_DIR = Path(tempfile.mkdtemp())  # no notes from voice being off
new_session.waiting_elsewhere.__defaults__ = ("", new_session.WAITING_DIR, None)
results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def focus_on(session, title, at):
    d = APP / "acct" / "org"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"local_{session}.json"
    f.write_text(json.dumps({"cliSessionId": session, "title": title, "lastFocusedAt": at}))
    os.utime(f, (at, at))


class StubTalker:
    def on_session_event(self, ev):
        FEED.add(ev)


async def main():
    focus_on("A", "Editor", 100)
    focus_on("B", "Release", 50)
    shown = []
    focus.FocusWatcher.__init__.__defaults__ = (APP, 0.05)
    runner = await daemon.serve_events(StubTalker(), SOCK, on_event=lambda: shown.append(daemon.waiting_elsewhere()))
    s = aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=SOCK))
    send = lambda ev: s.post("http://voice/event", json=ev)
    settle = lambda: asyncio.sleep(0.3)

    ok(not daemon.waiting_elsewhere(), "nothing open: no stroke")
    await send({"type": "permission", "session": "B", "what": "run git push"})
    await settle()
    ok(daemon.waiting_elsewhere() and shown[-1], "another session asks a permission: the orb shows it")
    await send({"type": "permission_done", "session": "B"})
    await settle()
    ok(not daemon.waiting_elsewhere() and not shown[-1], "allowed or denied there: gone")

    await send({"type": "question", "session": "B", "id": "q1", "questions": [{"question": "Which?", "options": ["A", "B"]}]})
    await settle()
    ok(daemon.waiting_elsewhere(), "a question form in another session: shown")
    await send({"type": "question_done", "session": "B", "id": "q1"})
    await settle()
    ok(not daemon.waiting_elsewhere(), "answered there: gone")

    await send({"type": "question", "session": "B", "id": "q2", "questions": [], "buttons": ["Yes", "Not now"]})
    await settle()
    ok(not daemon.waiting_elsewhere(), "a turn ending with a closing question: not shown (it comes with every turn's end)")

    await send({"type": "question", "session": "A", "id": "q3", "questions": [{"question": "Tea?", "options": ["Yes"]}]})
    await settle()
    ok(not daemon.waiting_elsewhere(), "the session you're in asks: not shown, you see its card there")
    focus_on("B", "Release", 200)  # you move to B without answering A
    await asyncio.sleep(0.5)
    ok(daemon.OUTBOX.target == "B" and daemon.waiting_elsewhere() and shown[-1],
       "you leave it unanswered: shown from the next session")
    await send({"type": "question_done", "session": "A", "id": "q3"})
    await settle()
    ok(not daemon.waiting_elsewhere() and not shown[-1], "answered later: gone")

    await send({"type": "permission", "session": "Z", "what": "edit a file"})
    await asyncio.sleep(1.5)
    ok(not daemon.waiting_elsewhere(), "a session the app doesn't show: dropped, never stuck on")

    daemon.WAITS.seed("C")
    ok(daemon.waiting_elsewhere(), "one that began waiting while voice was off: shown")
    await send({"type": "turn_start", "session": "C"})
    await settle()
    ok(not daemon.waiting_elsewhere(), "until it carries on")

    await s.close()
    await runner.cleanup()


asyncio.run(main())

# What the orb gets: set_orb carries the flag in the "S" message, sent only when something changed.
sent = []
helper = AudioHelper.__new__(AudioHelper)
helper._orb = {"voice": "listening", "busy": False, "opening": False, "hidden": False, "muted": False, "waiting": False}
helper._send = lambda kind, payload=b"": sent.append((kind, payload))
helper.set_orb(busy=False, waiting=True)
ok(sent and sent[-1][0] == b"S" and json.loads(sent[-1][1])["waiting"] is True, "the orb is told")
helper.set_orb(busy=False, waiting=True)
ok(len(sent) == 1, "the same state again: nothing resent")
helper.set_orb(busy=False, waiting=False)
ok(json.loads(sent[-1][1])["waiting"] is False, "and when it's answered")

print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
