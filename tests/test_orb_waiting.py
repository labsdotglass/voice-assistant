"""The orb shows when the session waits on you (card #14), offline: the flag goes on with a permission or a
question, off once each is answered, off when you move to another session, and reaches the orb's "S" message."""

import _root  # noqa: F401  (the repo root on the import path)
import json
import sys

from helper_transport import AudioHelper
from talker import FEED

results = []
ME, OTHER = "aaaa1111-0000-0000-0000-000000000001", "bbbb2222-0000-0000-0000-000000000002"


def ok(cond, msg):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


FEED.add({"type": "switched", "session": ME, "title": "Editor", "busy": True, "initial": True})
ok(not FEED.waiting_on_you, "nothing open: not waiting on you")
FEED.add({"type": "permission", "session": ME, "id": "p1", "what": "edit daemon.py"})
ok(FEED.waiting_on_you, "a permission: waiting on you")
FEED.add({"type": "permission_done", "session": ME, "id": "p1"})
ok(not FEED.waiting_on_you, "allowed or denied: not any more")
FEED.add({"type": "question", "session": ME, "id": "q1", "questions": [{"question": "Which?", "options": ["A", "B"]}]})
ok(FEED.waiting_on_you, "a question form: waiting on you")
FEED.add({"type": "question_done", "session": ME, "id": "q1"})
ok(not FEED.waiting_on_you, "answered: not any more")
FEED.add({"type": "question", "session": ME, "id": "q2", "questions": [], "buttons": ["Yes", "Not now"]})
ok(FEED.waiting_on_you, "a turn that ends with a closing question: waiting on you")
FEED.add({"type": "switched", "session": OTHER, "title": "Other", "busy": False})
ok(not FEED.waiting_on_you, "moved to another session: off")

# What the orb gets: set_orb carries the flag in the "S" message, sent only when something changed.
sent = []
helper = AudioHelper.__new__(AudioHelper)
helper._orb = {"voice": "listening", "busy": False, "opening": False, "hidden": False, "muted": False, "waiting": False}
helper._send = lambda kind, payload=b"": sent.append((kind, payload))
helper.set_orb(busy=False, waiting=True)
ok(sent and sent[-1][0] == b"S" and json.loads(sent[-1][1])["waiting"] is True, "the orb is told it's waiting")
helper.set_orb(busy=False, waiting=True)
ok(len(sent) == 1, "the same state again: nothing resent")
helper.set_orb(busy=False, waiting=False)
ok(json.loads(sent[-1][1])["waiting"] is False, "and when it's answered")

print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
