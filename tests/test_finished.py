"""Other sessions finishing, without the model: the notes voice reads when it starts (which are stale and
removed), the app's summary only once it's the last turn's, the talker remembering those sessions across a
switch and forgetting the one you went to, and the news waiting for a second of quiet after voice speaks."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import new_session
import talker
from talker import FEED

results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def record(folder: Path, name: str, **fields) -> Path:
    path = folder / "acct" / "org" / f"local_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cwd": f"/u/dev/{name}", **fields}))
    return path


def notes():
    tmp = Path(tempfile.mkdtemp())
    apps, notes_dir = tmp / "apps", tmp / "notes"
    notes_dir.mkdir()
    now = time.time()
    finished_ms = (now - 60) * 1000

    def note(sid: str, path: Path, at_ms: float = finished_ms):
        (notes_dir / f"{sid}.json").write_text(json.dumps({"id": sid, "record": str(path), "at": at_ms, "answer": "Done. All good."}))

    summary = {"status_category": "completed", "status_detail": "dev server restarted"}
    note("site", record(apps, "site", cliSessionId="site", title="Website setup", lastFocusedAt=finished_ms - 5000,
                        lastAssistantUuid="u2", postTurnSummaryFor="u2", postTurnSummary=summary))
    note("old-summary", record(apps, "old", cliSessionId="old-summary", title="Old summary", lastFocusedAt=0,
                               lastAssistantUuid="u3", postTurnSummaryFor="u1", postTurnSummary=summary))
    note("visited", record(apps, "visited", cliSessionId="visited", title="Visited since", lastFocusedAt=finished_ms + 5000))
    note("archived", record(apps, "archived", cliSessionId="archived", title="Archived", isArchived=True))
    note("stale", record(apps, "stale", cliSessionId="stale", title="A day old"), at_ms=(now - 2 * 86400) * 1000)
    note("me", record(apps, "me", cliSessionId="me", title="This one"))

    rows = new_session.finished_elsewhere("me", folder=notes_dir, now=now)
    ok(sorted(r["id"] for r in rows) == ["old-summary", "site"], f"only unseen notes from other sessions are told ({[r['id'] for r in rows]})")
    by_id = {r["id"]: r for r in rows}
    ok((by_id.get("site") or {}).get("summary") == {"status": "completed", "detail": "dev server restarted", "needs": ""},
       "the app's summary is used when it's the last turn's")
    ok((by_id.get("old-summary") or {}).get("summary", "missing") is None, "a summary of the turn before isn't passed off as this one's")
    left = sorted(p.stem for p in notes_dir.glob("*.json"))
    ok(left == ["me", "old-summary", "site"], f"visited, archived and day-old notes are removed ({left})")

    found = new_session.turn_summary("site", root=apps)
    ok(found == {"title": "Website setup", "project": "site", "summary": {"status": "completed", "detail": "dev server restarted", "needs": ""}},
       f"turn_summary finds a session by its id ({found})")
    ok(new_session.turn_summary("nobody", root=apps) is None, "turn_summary: None for a session the app doesn't show")


def feed():
    FEED.pending, FEED.elsewhere = [], []
    FEED.add({"type": "finished_elsewhere", "session": "s1", "title": "Website setup", "project": "site",
              "summary": {"status": "blocked", "detail": "", "needs": "pick a port"}, "answer": ""})
    note = FEED.pending[-1]
    ok("Website setup" in note and "s1" in note and "waiting on the user: pick a port" in note,
       "a blocked session's news says what it needs")
    FEED.add({"type": "finished_elsewhere", "session": "s2", "title": "Docs", "project": "docs", "summary": None,
              "answer": "I rewrote the README."})
    ok("Its answer: I rewrote the README." in FEED.pending[-1], "before the app's summary, the session's answer stands in")
    ok([e["id"] for e in FEED.elsewhere] == ["s2", "s1"], "the sessions that finished are remembered, newest first")
    FEED.add({"type": "switched", "session": "s1", "title": "Website setup", "project": "site"})
    ok([e["id"] for e in FEED.elsewhere] == ["s2"], "going to one of them forgets it")
    ok(any("Docs" in p and "already told" in p for p in FEED.pending),
       "after a switch the talker still knows the other one, as background, not news")
    FEED.pending, FEED.elsewhere = [], []
    FEED.add({"type": "finished_while_off", "sessions": [
        {"id": "a", "title": "Website setup", "project": "site", "summary": {"status": "completed", "detail": "pushed"}, "answer": ""},
        {"id": "b", "title": "Docs", "project": "docs", "summary": None, "answer": "Done."}]})
    ok("while voice was off" in FEED.pending[-1] and "Website setup" in FEED.pending[-1] and "Docs" in FEED.pending[-1],
       "voice starting hears every session that finished while it was off")
    ok([e["id"] for e in FEED.elsewhere] == ["a", "b"], "and remembers them, newest first")
    FEED.pending, FEED.elsewhere = [], []


async def quiet():
    """The news waits while voice speaks, then for QUIET_BEFORE_NEWS of listening; you speaking starts it over."""
    told = []
    replying = {"on": True}

    async def reply(text):
        told.append(time.monotonic())

    stub = SimpleNamespace(_replying=lambda: replying["on"], _reply=reply, create_task=asyncio.ensure_future, _reply_task=None)
    FEED.pending = ["[news]"]
    talker.VOICE_STATE["now"] = "speaking"
    task = asyncio.ensure_future(talker.Talker._news_when_quiet(stub))
    await asyncio.sleep(0.5)
    replying["on"] = False  # the reply's text is done, its audio still playing
    await asyncio.sleep(0.5)
    ok(not told, "not while voice is still speaking")
    talker.VOICE_STATE["now"] = "listening"
    await asyncio.sleep(0.5)
    talker.VOICE_STATE["now"] = "hearing"  # you start talking
    await asyncio.sleep(0.3)
    talker.VOICE_STATE["now"] = "listening"
    quiet_again = time.monotonic()
    await asyncio.wait_for(task, 3)
    await asyncio.sleep(0.05)
    waited = told[0] - quiet_again if told else -1
    ok(len(told) == 1 and waited >= talker.QUIET_BEFORE_NEWS - 0.05,
       f"told once, a second after voice went quiet again, not counting the quiet before you spoke ({waited:.2f}s)")
    FEED.pending, told[:] = [], []
    await asyncio.wait_for(talker.Talker._news_when_quiet(stub), 3)
    ok(not told, "nothing said when a reply meanwhile already carried the news")


notes()
feed()
asyncio.run(quiet())
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
