"""Other sessions waiting for you, offline: a wait is told once until its session moves on; what it asks is put in
a few words; the notes left while voice is off are read back and dropped once stale; and the talker gets a news
item it tells you about unprompted."""

import _root  # noqa: F401  (the repo root on the import path)
import json
import sys
import tempfile
import time
from pathlib import Path

import new_session
from talker import FEED

results = []
ME, SITE = "aaaa1111-0000-0000-0000-000000000001", "bbbb2222-0000-0000-0000-000000000002"


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


waits = new_session.Waits()
perm = {"type": "permission", "session": SITE, "what": "edit daemon.py"}
ok(waits.new(perm) and not waits.new(dict(perm)), "a permission request is told once, its duplicate isn't")
ok(waits.new({**perm, "what": "run the tests"}), "a different request in the same session is told")
waits.moved_on(SITE)
ok(waits.new(perm), "once the session moves on (answered, a new turn), the same request is told again")
form = {"type": "question", "session": SITE, "id": "toolu_1", "questions": [{"question": "Which layout?"}]}
ok(waits.new(form) and not waits.new(form), "a question form is told once")

ok(new_session.wait_described(perm) == "your permission to edit daemon.py", "a permission in a few words")
ok(new_session.wait_described(form) == "an answer: Which layout?", "a question form by its question")
ok(new_session.wait_described({"type": "question"}) == "an answer to its question", "a question with no text")

# Notes left while voice was off: read back, newest first; your own session, a visited one, an old one dropped.
tmp = Path(tempfile.mkdtemp())
notes, records = tmp / "waiting", tmp / "records"
notes.mkdir(), records.mkdir()
now = time.time()


def note(session, title, at, focused=0.0, archived=False, what="your permission to edit daemon.py"):
    record = records / f"{session}.json"
    record.write_text(json.dumps({"title": title, "cwd": f"/u/dev/{title.split()[0].lower()}", "lastFocusedAt": focused * 1000,
                                  "isArchived": archived}))
    (notes / f"{session}.json").write_text(json.dumps({"id": session, "record": str(record), "at": at * 1000, "what": what}))


note(SITE, "Website setup", now - 60)
note("cccc3333", "Docs pass", now - 30, what="an answer: Which layout?")
note(ME, "Project overview", now - 10)
note("dddd4444", "Visited since", now - 300, focused=now - 100)
note("eeee5555", "Old one", now - 2 * 86400)
rows = new_session.waiting_elsewhere(ME, folder=notes, now=now)
ok([r["title"] for r in rows] == ["Docs pass", "Website setup"], f"waiting notes, newest first, not your own: {[r['title'] for r in rows]}")
ok(rows[0]["what"] == "an answer: Which layout?" and rows[1]["project"] == "website", "each with what it wants and its project")
ok(not (notes / "dddd4444.json").exists() and not (notes / "eeee5555.json").exists(),
   "a session visited since, or a day-old note, is dropped")

# The talker's news item: told unprompted, by name, with what it wants and the id for go_to_session only.
FEED.pending = []
worth = FEED.add({"type": "waiting_elsewhere", "session": SITE, "title": "Website setup", "project": "acme-site",
                  "what": "your permission to edit daemon.py"})
item = FEED.pending[-1] if FEED.pending else ""
ok(worth and "Website setup" in item and "your permission to edit daemon.py" in item and SITE in item,
   "the talker gets a news item to tell unprompted, with the session's name, what it wants and its id")
FEED.pending = []
worth = FEED.add({"type": "waiting_while_off", "sessions": rows})
ok(worth and "Docs pass" in FEED.pending[-1] and "Website setup" in FEED.pending[-1],
   "at start, the sessions waiting since voice was off are one news item")

# A wait ending: Waits forgets it, and its news, not yet spoken, is withdrawn; a switch keeps other sessions' news.
w = new_session.Waits()
w.new(perm)
ok(w.still(perm) and w.moved_on(SITE) and not w.still(perm) and not w.moved_on(SITE),
   "a wait is open until its session moves on, and moving on reports whether there was one")
FEED.pending = []
FEED.add({"type": "waiting_elsewhere", "session": SITE, "title": "Website setup", "project": "acme-site", "what": "x"})
FEED.add({"type": "waiting_elsewhere", "session": "cccc3333", "title": "Docs pass", "project": "design-kit", "what": "y"})
FEED.add({"type": "wait_over", "session": SITE})
ok(len(FEED.pending) == 1 and "Docs pass" in FEED.pending[0], "a wait answered before voice told it: its news is withdrawn, others stay")
FEED.add({"type": "switched", "session": "ffff6666", "title": "Another", "project": "dev"})
ok(any("Docs pass" in p for p in FEED.pending), "switching sessions keeps another session's untold wait")
FEED.add({"type": "waiting_elsewhere", "session": "ffff6666", "title": "Another", "project": "dev", "what": "z"})
FEED.add({"type": "switched", "session": "ffff6666", "title": "Another", "project": "dev"})
ok(not any("ffff6666" in p and "is waiting" in p for p in FEED.pending), "switching to the waiting session drops its news: you see it there")

print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
