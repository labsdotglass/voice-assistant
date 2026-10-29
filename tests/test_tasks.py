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

for i in range(12):
    TASKS.waiting([{"id": f"x{i}", "text": f"Task {i}"}])
ok(len(TASKS.tasks) >= 10 and all(t["status"] == "waiting" for t in TASKS.tasks if t["id"].startswith("x")),
   "the list stays short by dropping finished ones first; waiting ones always stay")

print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
