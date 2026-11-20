"""Queued tasks for the orb's badge and panel: each one's status as it goes (no models, no audio)."""

import _root  # noqa: F401  (the repo root on the import path)
import sys

from talker import FEED, TASKS

results = []
seen = []
TASKS.on_change = seen.append


def ok(cond, msg):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + msg)


def statuses():
    return [(t["text"], t["status"]) for t in TASKS.snapshot()["tasks"]]


readme, log, tag = {"id": "q1", "text": "Update the README"}, {"id": "q2", "text": "Check the logs"}, {"id": "q3", "text": "Tag a release"}
FEED.add({"type": "turn_start", "text": "Refactor the focus watcher."})
FEED.add({"type": "queue", "items": [readme, log, tag]})
ok(TASKS.snapshot()["waiting"] == 3 and seen, "three prompts queued: the badge shows 3, and the orb is told")

FEED.add({"type": "turn_complete", "answer": "Refactored.", "durationMs": 1000, "isAborted": False})
FEED.add({"type": "queue", "items": [log, tag]})
FEED.add({"type": "queue_started", "id": "q1", "text": "Update the README"})
ok(statuses()[0] == ("Update the README", "running") and TASKS.snapshot()["waiting"] == 2, "the first one is sent: running, 2 still waiting")

FEED.queued = [log, tag]
TASKS.cancelled(["q3"])
ok(dict(statuses())["Tag a release"] == "cancelled" and TASKS.snapshot()["waiting"] == 1, "one taken back by voice: cancelled")

FEED.add({"type": "turn_complete", "answer": "Done.", "durationMs": 1000, "isAborted": False})
ok(dict(statuses())["Update the README"] == "done", "its turn finishes: done")

for i in range(12):  # the mod's whole queue each time, one more each time
    TASKS.waiting([{"id": f"x{j}", "text": f"Task {j}"} for j in range(i + 1)])
ok(len(TASKS.tasks) >= 10 and all(t["status"] == "waiting" for t in TASKS.tasks if t["id"].startswith("x")),
   "the list stays short by dropping finished ones first; waiting ones always stay")

# Two sessions: what happens in the one you left still moves its tasks on (the daemon passes them through).
TASKS.tasks.clear()
TASKS.busy.clear()
A, B = "sess-a", "sess-b"
merge, thick, push = {"id": "a1", "text": "Merge and push"}, {"id": "a2", "text": "Too thick"}, {"id": "a3", "text": "Merge into main"}
FEED.add({"type": "switched", "session": A, "title": "Editor", "busy": True})
FEED.add({"type": "queue", "session": A, "items": [merge]})
ok(TASKS.snapshot()["waiting"] == 1, "queued in the session you're in: the badge shows 1")

FEED.add({"type": "switched", "session": B, "title": "Other", "busy": False})
ok(TASKS.snapshot()["waiting"] == 0 and not [t for t in TASKS.snapshot()["tasks"] if not t["where"]],
   "in another session: the badge and that session's own tasks are none")
ok([t["where"] for t in TASKS.snapshot()["tasks"]] == ["Editor"], "and the one still waiting in the session you left is listed under its title (#9)")
for ev in ({"type": "turn_complete"}, {"type": "queue", "items": []}, {"type": "queue_started", "id": "a1", "text": "Merge and push"},
           {"type": "turn_start"}):
    TASKS.on_event({**ev, "session": A})  # what daemon.py does with another session's events
ok(TASKS._find("a1")["status"] == "running", "sent while you were elsewhere: running, not left waiting")
TASKS.on_event({"type": "turn_complete", "session": A})
ok(TASKS._find("a1")["status"] == "done", "and finished while you were elsewhere: done")

FEED.add({"type": "switched", "session": A, "title": "Editor", "busy": False})
ok(TASKS.snapshot()["waiting"] == 0 and dict(statuses())["Merge and push"] == "done", "back in it: done, the badge shows 0")

# Missed events (voice restarted meanwhile): the next queue snapshot settles what left the queue.
TASKS.waiting([thick, push], A)
TASKS.waiting([], A)
ok(all(TASKS._find(i)["status"] == "done" for i in ("a2", "a3")), "gone from an idle session's queue: done, never waiting forever")
TASKS.on_event({"type": "turn_start", "session": A})
TASKS.waiting([{"id": "a4", "text": "One more"}], A)
TASKS.waiting([], A)
ok(TASKS._find("a4")["status"] == "running", "gone from a working session's queue: running")
TASKS.on_event({"type": "turn_complete", "session": B})
ok(TASKS._find("a4")["status"] == "running", "another session's turn ending doesn't finish it")
TASKS.on_event({"type": "turn_complete", "session": A})
ok(TASKS._find("a4")["status"] == "done", "its own session's does")

# Card #9: the panel says where each task is. Yours first, then what's still open in your other sessions,
# under their project and session's title; the talker hears those too, to name the project when asked.
FEED.add({"type": "switched", "session": "s-api", "title": "Fix login", "project": "api", "busy": True})
TASKS.on_event({"type": "turn_start", "session": "s-api"})
TASKS.waiting([{"id": "p1", "text": "Add a rate limit"}], "s-api")
FEED.add({"type": "switched", "session": "s-web", "title": "New header", "project": "web", "busy": True})
TASKS.on_event({"type": "turn_start", "session": "s-web"})
TASKS.waiting([{"id": "p2", "text": "Make it sticky"}], "s-web")
snap = TASKS.snapshot()
mine = [t for t in snap["tasks"] if not t["where"]]
theirs = [t for t in snap["tasks"] if t["where"]]
ok([t["text"] for t in mine] == ["Make it sticky"] and snap["waiting"] == 1, "the session you're in: its tasks, unlabeled, and the badge counts only them")
ok([(t["where"], t["text"]) for t in theirs] == [("api · Fix login", "Add a rate limit")], "another session's open task: under its project and title")
ok(snap["tasks"].index(mine[0]) < snap["tasks"].index(theirs[0]), "yours first")
ok("api · Fix login: Add a rate limit (waiting)" in TASKS.elsewhere_note(), "the talker hears where the other open task is")
FEED.pending, FEED.queued, FEED.forms, FEED.permissions = [], [], {}, {}
ok(FEED.drain(you_spoke=False) == "", "alone, other sessions' tasks are no news: voice doesn't speak up to list them")
ok("api · Fix login" in FEED.drain(you_spoke=True), "but when you speak, the talker has them")
TASKS.waiting([], "s-api")  # sent: running, its session working
TASKS.on_event({"type": "turn_complete", "session": "s-api"})
ok(all(not t["where"] for t in TASKS.snapshot()["tasks"]), "once it's done there, it leaves your panel")
TASKS.show("s-x")
ok(TASKS.where("s-unknown") == "Another session", "a session voice never saw: still labeled")

print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
