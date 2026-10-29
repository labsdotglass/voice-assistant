"""Another session finishing, by voice (asks Haiku): voice says which session finished, by name,
never reading its id, with what it needs when it's blocked, and asks whether to go there; a yes goes there by
its id, a no leaves it. When voice starts after sessions finished while it was off, it names them."""

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

SITE = "bbbb2222-0000-0000-0000-000000000002"
DOCS = "cccc3333-0000-0000-0000-000000000003"
results, went = [], []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


async def turn(client, text=""):
    buf, out = io.StringIO(), []
    with redirect_stdout(buf):
        await client.query(talker.turn(FEED.drain(), text))
        async for m in client.receive_response():
            if isinstance(m, AssistantMessage):
                out.append(m)
    sent = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ") and '"submit"' in l]
    return heard_from(out), sent


def fresh():
    FEED.pending, FEED.queued, FEED.busy, FEED.elsewhere = [], [], False, []
    went.clear()
    FEED.add({"type": "switched", "initial": True, "session": "aaaa1111-0000-0000-0000-000000000001",
              "title": "Project overview", "project": "voice-assistant"})


async def main():
    talker.NEW_SESSION["go"] = lambda name, project, session_id="": went.append(session_id)
    site_done = {"type": "finished_elsewhere", "session": SITE, "title": "Website setup", "project": "acme-site",
                 "summary": {"status": "completed", "detail": "dev server restarted with --host", "needs": ""}, "answer": ""}

    for answer, want in [("Yes, take me there.", [SITE]), ("No, not now.", [])]:
        fresh()
        async with ClaudeSDKClient(options=make_options()) as c:
            FEED.add(site_done)
            said, sent = await turn(c)
            low = said.lower()
            ok("website" in low and "?" in said and SITE[:4] not in said and not sent and not went,
               "another session finished: it's named, no id read out, asked whether to go, nothing sent", said)
            said, sent = await turn(c, answer)
        ok(went == want and not sent, f"{answer!r} -> {'goes there by its id' if want else 'stays here'} ({went})", said)

    fresh()
    async with ClaudeSDKClient(options=make_options()) as c:
        FEED.add({"type": "finished_elsewhere", "session": DOCS, "title": "Docs rewrite", "project": "docs",
                  "summary": {"status": "blocked", "detail": "", "needs": "which license to use"}, "answer": ""})
        said, sent = await turn(c)
    ok("docs" in said.lower() and "license" in said.lower() and not sent,
       "a blocked session: it says what the session needs from you", said)

    fresh()
    async with ClaudeSDKClient(options=make_options()) as c:
        FEED.add({"type": "finished_while_off", "sessions": [
            {"id": SITE, "title": "Website setup", "project": "acme-site", "summary": site_done["summary"], "answer": ""},
            {"id": DOCS, "title": "Docs rewrite", "project": "docs", "summary": None, "answer": "I rewrote the README."}]})
        said, sent = await turn(c)
        low = said.lower()
        ok("website" in low and "docs" in low and SITE[:4] not in said and DOCS[:4] not in said and not sent,
           "voice starting: it names the sessions that finished while it was off", said)
        said, sent = await turn(c, "Go to the docs one.")
    ok(went == [DOCS] and not sent, f"and goes to the one you pick by its id ({went})", said)

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
