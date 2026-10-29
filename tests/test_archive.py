"""Archiving the session voice is in, by voice (asks Haiku): voice first asks where to go, a new
session or an existing one, sends nothing to this session, then moves there with archive_after set; once the
move is done the session it arrived in is asked to archive the one it left."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import new_session
import talker
from talker import ARCHIVE_AFTER, FEED, heard_from, make_options

results, opened = [], []
HERE = "[the session you're working with: \"Project overview\" in the voice-assistant project (ID, for prompts only: 1b2c3d4e-0000-4000-8000-000000000001).]"


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
    talker.NEW_SESSION["start"] = lambda message, folder, name: opened.append(("new", name))
    talker.NEW_SESSION["go"] = lambda name, project, session_id="": opened.append(("go", name))
    for ask, where, kind in [("Archive this session.", "A new one, please.", "new"),
                             ("Can you archive this session for me?", "Take me to the website setup session.", "go")]:
        FEED.pending, FEED.queued, FEED.busy = [HERE], [], False
        FEED.here = {"id": "1b2c3d4e-0000-4000-8000-000000000001", "title": "Project overview"}
        ARCHIVE_AFTER.update(id="", title="")
        opened.clear()
        async with ClaudeSDKClient(options=make_options()) as c:
            said, sent = await turn(c, ask)
            ok(not sent and not opened and "?" in said, f"{ask!r}: asks where to go first, sends and opens nothing", said)
            said, sent = await turn(c, where)
        ok(not sent and [o[0] for o in opened] == [kind], f"{where!r}: moves with {kind}, sends nothing to this session", said)
        ok(ARCHIVE_AFTER["id"] == FEED.here["id"], "the session left is marked to archive after the move")

    # Once the move is done, the session voice is in now gets the archive request, naming the one left.
    buf = io.StringIO()
    with redirect_stdout(buf):
        FEED.add({"type": "new_session_done", "ok": True, "text": "Opened 'website setup' (voice-assistant)."})
    sent = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if '"submit"' in l]
    ok(len(sent) == 1 and "archive" in sent[0]["text"].lower() and "Project overview" in sent[0]["text"]
       and not ARCHIVE_AFTER["id"], f"after the move, archiving is asked once ({sent[0]['text'] if sent else None!r})")
    ARCHIVE_AFTER.update(FEED.here)
    with redirect_stdout(io.StringIO()) as buf:
        FEED.add({"type": "new_session_done", "ok": False, "text": "The session didn't open."})
    ok('"submit"' not in buf.getvalue() and "not archived" in FEED.pending[-1], "a failed move archives nothing")

    # The archive request names the app's id ("local_…", what its archive tool takes), not the CLI id voice knows
    # the session by, which is often a different one; with no app record, it names the session by title alone.
    with tempfile.TemporaryDirectory() as root:
        record = Path(root, "a", "b", "local_9f00aa11-0000-4000-8000-000000000002.json")
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"cliSessionId": FEED.here["id"], "title": "Project overview"}))
        ok(new_session.app_id(FEED.here["id"], root=Path(root)) == record.stem, "the app's id is found from the CLI id")
        ok(new_session.app_id("no-such-session", root=Path(root)) == "", "no app record: no id")
        real = new_session.app_id
        for found, want in [(record.stem, f"(ID {record.stem})"), ("", "Project overview\";")]:
            new_session.app_id = lambda sid, found=found: found
            ARCHIVE_AFTER.update(FEED.here)
            with redirect_stdout(io.StringIO()) as buf:
                FEED.add({"type": "new_session_done", "ok": True, "text": "Opened it."})
            sent = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if '"submit"' in l]
            ok(len(sent) == 1 and want.replace('"', "") in sent[0]["text"].replace('"', "") and FEED.here["id"] not in sent[0]["text"],
               f"the archive request names the app's id when known ({sent[0]['text'] if sent else None!r})")
        new_session.app_id = real
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
