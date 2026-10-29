"""Everything in the orb's right-click menu, by voice (asks Haiku): each action said in plain words
reaches the menu's own handler (voice_menu), Stop voice ends voice, and work for the session still
goes to the session. Shared by the menu checks (test_voice_menu.py, test_menu_*.py), each running
a part in its own process so the runner can run them side by side."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import talker
from talker import ENDING, FEED, MENU, heard_from, make_options

results = []
asked: list[tuple[str, str]] = []


def handler(action: str, value: str) -> tuple[bool, str]:
    asked.append((action, value.strip().lower()))
    return True, "Done."


CASES = [  # (what you say, the action, the value it should carry or None)
    ("Show me the transcript.", "show_transcript", None),
    ("Hide the transcript, please.", "hide_transcript", None),
    ("Switch to Michael's voice.", "voice", "michael"),
    ("Can you talk in the Kore voice?", "voice", "kore"),
    ("Pass my words on cleaned up from now on.", "pass_on", "cleaned"),
    ("Go back to concise for passing on my words.", "pass_on", "concise"),
    ("Learn my voice again.", "learn_voice", None),
    ("Show me the tour.", "show_tour", None),
    ("Turn off the progress updates.", "progress", "off"),
    ("Turn progress updates back on.", "progress", "on"),
    ("Go to the next session.", "next_session", None),
    ("Switch to Hebrew.", "language", "hebrew"),
    ("Let's talk in Spanish from now on.", "language", "spanish"),
]


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


async def say(text: str) -> tuple[str, list[dict]]:
    FEED.pending, FEED.queued, FEED.forms, FEED.busy = [], [], {}, False
    ENDING.cancel_quietly()
    asked.clear()
    buf, out = io.StringIO(), []
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(buf):
            await client.query(FEED.drain() + text)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    out.append(msg)
    cmds = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
    return heard_from(out), [c for c in cmds if c["type"] in ("submit", "interrupt")]


async def menu_actions(cases):
    for text, action, value in cases:
        said, sent = await say(text)
        got = asked[0] if asked else None
        right = got is not None and got[0] == action and (value is None or value in got[1])
        ok(right and not sent and len(asked) == 1, f"{text!r} -> {action}{' ' + value if value else ''} (got {asked}, sent {sent})", said)


async def stop_voice():
    said, sent = await say("Stop voice.")
    ok(ENDING.requested and not asked and not sent, "\"Stop voice\" turns voice mode off (end_voice)", said)
    ENDING.cancel_quietly()


async def sessions():
    # A new session by voice: started with its first message, in the folder named.
    started = []
    talker.NEW_SESSION["start"] = lambda message, folder, name: started.append((message, folder.lower(), name))
    for text, folder, words in [("Start a new session in dev and have it check the git status of every repo.", "", "git"),
                                ("Open a new session in design kit and tell it to run the tests.", "design", "test"),
                                ("Open a new session.", "", None)]:
        started.clear()
        said, sent = await say(text)
        got = started[0] if started else None
        right = got is not None and (folder in got[1] if folder else got[1] in ("", "dev")) and (words is None or words in got[0].lower()) and got[2]
        ok(right and not sent and not asked, f"{text!r} starts a new session: {got}", said)

    # Going to an existing session by name.
    went = []
    talker.NEW_SESSION["go"] = lambda name, project, session_id="": went.append((name.lower(), project.lower()))
    for text, name, project in [("Go to the uncommitted changes review session.", "uncommitted", ""),
                                ("Switch me to the second brain session in Atlas.", "second brain", "atlas"),
                                ("Take me to the payments api project.", "", "payments")]:
        went.clear()
        started.clear()
        said, sent = await say(text)
        got = went[0] if went else None
        ok(got is not None and name in got[0] and project in got[1] and not started and not sent and not asked,
           f"{text!r} goes to that session: {got}", said)


async def work_for_session():
    said, sent = await say("Have it start a new branch for the login fix.")
    ok(not asked and [c["type"] for c in sent] == ["submit"], f"a new branch is work for the session, not a new session: {sent}", said)

    said, sent = await say("Have the session clear its build cache.")
    ok(not asked and [c["type"] for c in sent] == ["submit"], f"work for the session still goes to it: {sent}", said)


async def tour():
    # The tour, while it's open: its buttons, said. (A stand-in open file holding this process's pid.)
    real_open = talker.TOUR_OPEN
    talker.TOUR_OPEN = Path(tempfile.mkdtemp()) / "tour-open"
    talker.TOUR_OPEN.write_text(str(os.getpid()))
    for text, value in [("Next.", "next"), ("Go to step three.", "3"), ("Go back.", "back"), ("Okay, close the tour.", "done")]:
        said, sent = await say(text)
        got = asked[0] if asked else None
        fits = value in got[1] or (value == "done" and "close" in got[1]) if got else False  # "close" is done, to the tour
        ok(got is not None and got[0] == "tour_step" and fits and not sent, f"tour open: {text!r} -> tour_step {value} (got {asked})", said)
    # Stepping to a slide: nothing said at all (the tour plays a soft sound), the slide not read out.
    slide = ("slide 2 of 7: Just talk. There's no button to hold. It listens even while it speaks, so you can cut in "
             "any time. It answers only your voice.")
    real_do = MENU["do"]
    MENU["do"] = lambda action, value: (asked.append((action, value)), (True, slide if action == "tour_step" else "Done."))[1]
    for _ in range(3):
        said, sent = await say("Next.")
        ok(not said.strip() and any(a[0] == "tour_step" for a in asked), "a step says nothing: no ack, no slide read out", said)
    MENU["do"] = real_do
    talker.TOUR_OPEN.unlink()
    said, sent = await say("Next.")
    ok(not any(a[0] == "tour_step" for a in asked), f"tour closed: 'Next.' doesn't step a tour (got {asked}, sent {sent})", said)
    talker.TOUR_OPEN = real_open


def run(*parts):
    """Run these parts in order, then report and exit as a check."""
    async def main():
        MENU["do"] = handler
        for part in parts:
            await part()
        print(f"{sum(results)}/{len(results)} passed")
        sys.exit(0 if all(results) else 1)

    asyncio.run(main())
