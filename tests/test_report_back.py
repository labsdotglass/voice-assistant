"""Every request gets a report once it's done: what came of it and where things stand. Offline: the turn's end names
the request and asks for the state, an ask with only the app's buttons counts as waiting on you, and a turn that ended
during a restart is news once voice is back, unless it was already told. By voice (asks Haiku): a recap naming a next
step is reported as done and waiting, a finished request as done, and a turn missed in a restart is told on return."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import re
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import talker
from talker import FEED, RESTART, heard_from, make_options, restart_note, unreported_request

talker.RESTART_MARKER = Path(tempfile.mkdtemp()) / "restart-reason"  # not the live daemon's
talker.GREETINGS = Path(tempfile.mkdtemp()) / "greetings.json"
ASKED = "Open a ticket for the login bug."
results = []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


def fresh():
    FEED.pending, FEED.queued, FEED.forms, FEED.buttons, FEED.busy, FEED.request = [], [], {}, {}, False, ""
    FEED.quiet_next = False
    RESTART.update(why="", language="", restored=False)
    talker.app_sessions.last_said = lambda sid: {}


def finished(answer: str, request: str = ASKED) -> str:
    fresh()
    FEED.add({"type": "turn_start", "text": request})
    FEED.pending = []
    FEED.add({"type": "turn_complete", "answer": answer, "durationMs": 9000})
    return "\n".join(FEED.pending)


def offline():
    note = finished("Ticket 12 is open.")
    ok(ASKED in note, "the turn's end names the request it was working on")
    ok("waiting on them" in note and "where things stand" in note, "and asks for the state after the outcome")
    FEED.pending = []
    FEED.add({"type": "turn_complete", "answer": "Done.", "durationMs": 1000})
    ok(ASKED not in "\n".join(FEED.pending), "the next turn isn't tied to a request it didn't work on")

    fresh()
    FEED.add({"type": "turn_start", "text": ASKED})
    FEED.add({"type": "switched", "session": "s2", "title": "Other", "project": "p"})
    FEED.pending = []
    FEED.add({"type": "turn_complete", "answer": "Done.", "durationMs": 1000})
    ok(ASKED not in "\n".join(FEED.pending), "a switch forgets the request of the session you left")

    fresh()
    worth = FEED.add({"type": "asks", "text": "Should I merge PR 18?"})
    ok(not worth and "Should I merge PR 18?" in FEED.pending[0], "an ask with only the app's buttons is told with the turn's end, not on its own")
    ok("on screen" in FEED.pending[0], "and the talker knows it's answered on screen")

    at = time.time()
    sent = {"who": "sent", "text": ASKED, "at": (at - 60) * 1000}
    ack = {"who": "talker", "text": "On it.", "at": (at - 59) * 1000}
    ok(unreported_request([sent, ack], at - 10) == ASKED, "a reply after voice last spoke: that request was never reported")
    ok(unreported_request([sent, ack, {"who": "talker", "text": "It's open.", "at": (at - 5) * 1000}], at - 10) == "", "already told: nothing to report")
    ok(unreported_request([ack], at - 10) == "", "nothing was sent: nothing to report")
    ok(unreported_request([sent, ack], None) == "", "no reply since: nothing to report")

    def history(busy: bool, reply: float) -> bool:
        fresh()
        RESTART.update(why="the mod reloaded")
        FEED.busy = busy
        talker.app_sessions.last_said = lambda sid: {"reply": reply}
        return FEED.add({"type": "history", "messages": [], "answer": "Ticket 12 is open.", "voice": [sent, ack]})

    ok(history(False, at - 10) and any("while voice was restarting" in p and ASKED in p for p in FEED.pending), "a turn that ended during a restart is news once voice is back")
    ok(not history(True, at - 10), "still working: not reported as done")
    ok(not history(False, at - 120), "the reply came before the request: not this request's report")


async def turn(client, text):
    out, buf = [], io.StringIO()
    with redirect_stdout(buf):
        await client.query(text)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                out.append(msg)
    return heard_from(out)


async def told(events: str) -> str:
    async with ClaudeSDKClient(options=make_options()) as client:
        return await turn(client, talker.turn(events))


async def by_voice():
    said = await told(FEED.drain() if finished("Ticket 12 is open and sits in Ready. Building it needs your go-ahead.") is not None else "")
    ok(bool(re.search(r"ticket|card", said, re.I) and re.search(r"go.?ahead|waiting|your (call|ok|word)|should|want|shall", said, re.I)),
       "a recap naming a next step: done and waiting on you", said)

    said = await told(FEED.drain() if finished("Ticket 12 is open. Nothing else to do.") is not None else "")
    ok(bool(re.search(r"ticket|card", said, re.I) and not re.search(r"your go.?ahead", said, re.I)), "a finished request: reported as done", said)

    at = time.time()
    fresh()
    RESTART.update(why="the mod reloaded")
    talker.app_sessions.last_said = lambda sid: {"reply": at - 10}
    FEED.add({"type": "history", "messages": [], "answer": "Ticket 12 is open.",
              "voice": [{"who": "sent", "text": ASKED, "at": (at - 60) * 1000}, {"who": "talker", "text": "On it.", "at": (at - 59) * 1000}]})
    FEED.pending.append(restart_note())
    said = await told(FEED.drain())
    ok(bool(re.search(r"ticket|12", said, re.I)), "after a restart, the missed turn is told", said)


offline()
if "--offline" not in sys.argv:
    asyncio.run(by_voice())
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
