"""Model and effort by voice (asks Haiku, card #10): switching the session you're in goes to it as one plain
settings request naming the model and effort, nothing opened; a new session asked for on a model gets it as
new_session's arguments; and asked which model this is, voice says the one the session reported."""

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

results, opened = [], []
HERE = "[the session you're working with: \"Project overview\" in the voice-assistant project.]"


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


def fresh(*pending):
    FEED.pending, FEED.queued, FEED.busy, FEED.forms, FEED.permissions = [HERE, *pending], [], False, {}, {}
    opened.clear()


async def main():
    talker.NEW_SESSION["start"] = lambda message, folder, name, model="", effort="": opened.append(("new", model, effort))
    talker.NEW_SESSION["go"] = lambda name, project, session_id="": opened.append(("go", name))

    for said_aloud, want in [("Switch this session to Sonnet with low effort.", ("sonnet", "low")),
                             ("Use max effort here.", ("max",)),
                             ("Can you put this session on Haiku?", ("haiku",))]:
        fresh()
        async with ClaudeSDKClient(options=make_options()) as c:
            said, sent = await turn(c, said_aloud)
        text = sent[0]["text"].lower() if sent else ""
        ok(len(sent) == 1 and all(w in text for w in want) and not opened,
           f"{said_aloud!r}: one request to the session naming {want} ({text!r}), nothing opened", said)

    fresh()
    async with ClaudeSDKClient(options=make_options()) as c:
        said, sent = await turn(c, "Start a new session in voice-assistant on Opus with high effort to look at the orb.")
    ok(opened and opened[0][0] == "new" and opened[0][1].lower() == "opus" and opened[0][2].lower() == "high" and not sent,
       f"a new session on a model: new_session gets them ({opened})", said)

    fresh("[background, not news: the session runs on Sonnet 5.5. Mention it only when the user asks, by that name]")
    async with ClaudeSDKClient(options=make_options()) as c:
        said, sent = await turn(c, "Which model is this session on?")
    ok("sonnet" in said.lower() and "claude-" not in said.lower() and not sent, "asked which model: says the one reported, by name, sends nothing", said)

    fresh()  # no model reported (an older plugin, or before it arrives)
    async with ClaudeSDKClient(options=make_options()) as c:
        said, sent = await turn(c, "Which model is this session on?")
    ok(not any(m in said.lower() for m in ("opus", "sonnet", "haiku", "fable")) and not sent,
       "none reported: no guess, nothing sent unasked", said)

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
