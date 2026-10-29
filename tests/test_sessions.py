"""What sessions are there, by voice (asks Haiku): voice lists your sessions by name, never
reading ids, and goes to the one you then pick, by description, by its place in the list, or among two
with the same name, passing its id."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
import time
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import new_session
import talker
from talker import FEED, heard_from, make_options

NOW = time.time()
ROWS = [
    {"id": "aaaa1111-0000-0000-0000-000000000001", "cwd": "/u/dev/voice-assistant", "title": "Project overview", "project": "voice-assistant", "last": NOW - 60},
    {"id": "bbbb2222-0000-0000-0000-000000000002", "cwd": "/u/dev/acme-site", "title": "Website setup", "project": "acme-site", "last": NOW - 600},
    {"id": "cccc3333-0000-0000-0000-000000000003", "cwd": "/u/Atlas", "title": "Second brain architecture brainstorm", "project": "Atlas", "last": NOW - 90000},
    {"id": "dddd4444-0000-0000-0000-000000000004", "cwd": "/u/Atlas", "title": "Voice-controlled work assistant", "project": "Atlas", "last": NOW - 95000},
    {"id": "eeee5555-0000-0000-0000-000000000005", "cwd": "/u/Atlas", "title": "Voice-controlled work assistant", "project": "Atlas", "last": NOW - 99000},
]
results, went = [], []


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
    new_session.list_sessions = lambda limit=12, root=None: ROWS[:limit]
    talker.NEW_SESSION["go"] = lambda name, project, session_id="": went.append(session_id)
    for pick, want in [("Go to the second brain one.", ROWS[2]["id"]), ("The second one, please.", ROWS[1]["id"]),
                       ("The older of the two voice-controlled ones.", ROWS[4]["id"])]:
        FEED.pending, FEED.queued, FEED.busy = [], [], False
        went.clear()
        async with ClaudeSDKClient(options=make_options()) as c:
            said, sent = await turn(c, "What sessions do I have?")
            ok("overview" in said.lower() and "website" in said.lower() and not any(r["id"][:4] in said for r in ROWS) and not sent,
               "asked what sessions there are, it names them, no ids, nothing sent", said)
            said, sent = await turn(c, pick)
        ok(went == [want] and not sent, f"{pick!r} goes to that session by its id ({went})", said)

    # Live, 27 Oct: sessions named by card, and "show me the number four" went to the session as work.
    cards = [{"id": "ffff6666-0000-0000-0000-000000000006", "cwd": "/u/dev/voice-assistant", "title": "Card #10: voice carries on after a restart", "project": "voice-assistant", "last": NOW - 60},
             {"id": "abab7777-0000-0000-0000-000000000007", "cwd": "/u/dev/voice-assistant", "title": "Card #4: guardrails for new features", "project": "voice-assistant", "last": NOW - 120}]
    new_session.list_sessions = lambda limit=12, root=None: cards[:limit]
    FEED.pending, FEED.queued, FEED.busy = [], [], False
    went.clear()
    async with ClaudeSDKClient(options=make_options()) as c:
        await turn(c, "What sessions do I have?")
        said, sent = await turn(c, "Show me the number four.")
        asked = not went and "?" in said
        ok(not sent and (asked or went == [cards[1]["id"]]),
           f"a request that could mean a session by its number: voice asks or goes there, and sends nothing ({went})", said)
        if asked:
            said, sent = await turn(c, "Yes.")
    ok(went == [cards[1]["id"]] and not sent, f"it ends in the Card #4 session ({went})", said)
    went.clear()
    async with ClaudeSDKClient(options=make_options()) as c:
        await turn(c, "What sessions do I have?")
        said, sent = await turn(c, "Show me the failing tests.")
    ok(len(sent) == 1 and not went, "plain work that names no session is still sent, without asking", said)

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
