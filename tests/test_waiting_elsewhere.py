"""Another session waiting for you, by voice (asks Haiku): voice says which session waits, by name,
never reading its id, and what it wants, asks whether to go there and sends nothing; a yes goes there by its id.
When voice starts after sessions began waiting while it was off, it names them."""

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

    fresh()
    async with ClaudeSDKClient(options=make_options()) as c:
        FEED.add({"type": "waiting_elsewhere", "session": SITE, "title": "Website setup", "project": "acme-site",
                  "what": "your permission to run npm install"})
        said, sent = await turn(c)
        low = said.lower()
        ok("website" in low and ("permission" in low or "allow" in low or "approv" in low or "npm" in low or "install" in low)
           and "?" in said and SITE[:4] not in said and not sent and not went,
           "another session waits for a permission: named, what it wants, asked whether to go, nothing sent", said)
        said, sent = await turn(c, "Yes, take me there.")
    ok(went == [SITE] and not sent, f"'Yes, take me there.' goes there by its id ({went})", said)

    fresh()
    async with ClaudeSDKClient(options=make_options()) as c:
        FEED.add({"type": "waiting_while_off", "sessions": [
            {"id": DOCS, "title": "Docs pass", "project": "design-kit", "what": "an answer: Which layout should the guide use?"},
            {"id": SITE, "title": "Website setup", "project": "acme-site", "what": "your permission to run npm install"}]})
        said, sent = await turn(c)
        low = said.lower()
        ok("docs" in low and "website" in low and not sent and DOCS[:4] not in said,
           "voice starting: it names the sessions waiting since it was off, nothing sent", said)

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
