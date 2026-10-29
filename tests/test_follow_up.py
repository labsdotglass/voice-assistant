"""A follow-up said while voice's last prompt still waits (an addition, a correction) joins that prompt, so the
session gets them as one message. Offline: the tool merges into the waiting prompt, idle or once it started
it goes on its own. Then by voice (asks Haiku): a request and its correction a few seconds later, while the
session is busy, become one waiting prompt holding both, and the ack fits."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import talker
from talker import ENDING, FEED, LAST_SENT, heard_from, make_options, send_to_session

results = []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


def commands(out: str) -> list[dict]:
    return [c for c in (json.loads(l[8:]) for l in out.splitlines() if l.startswith("@@VOICE ")) if c["type"] in ("submit", "merge")]


def held(cmds: list[dict]):
    """What the mod does with a submit while the session is busy: holds it, and tells voice its queue."""
    for c in cmds:
        if c["type"] == "submit":
            FEED.add({"type": "queue", "items": FEED.queued + [{"id": c["id"], "text": c["text"]}]})


async def send(prompt: str) -> tuple[list[dict], str]:
    LAST_SENT.update(text="", at=0.0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        reply = await send_to_session.handler({"ack": "", "prompt": prompt})
    return commands(buf.getvalue()), reply["content"][0]["text"]


def fresh(busy: bool):
    FEED.pending, FEED.queued, FEED.forms, FEED.busy = [], [], {}, busy
    ENDING.cancel_quietly()


async def offline():
    fresh(busy=True)
    first, _ = await send("Run the Slack work skill.")
    held(first)
    second, reply = await send("Not Slack work, I meant track work.")
    ok([c["type"] for c in first] == ["submit"] and [c["type"] for c in second] == ["merge"],
       f"busy: the correction merges into the waiting prompt ({[c['type'] for c in first + second]})")
    ok(second and second[0]["id"] == first[0]["id"] and "Slack work skill" in second[0]["text"]
       and "track work" in second[0]["text"] and second[0]["addition"] == "Not Slack work, I meant track work.",
       "the merged prompt holds both, in order, and the addition alone for if it started meanwhile")
    ok("waiting" in reply.lower(), f"the tool's reply says it joined the waiting prompt: {reply!r}")

    fresh(busy=False)
    first, _ = await send("Make the orb smaller.")
    FEED.busy = True  # the first one went straight in: the session is on it now
    second, _ = await send("And a bit lighter.")
    ok([c["type"] for c in first + second] == ["submit", "submit"],
       "idle: the first goes, the follow-up to running work goes on its own")


async def by_voice():
    fresh(busy=True)
    buf, said = io.StringIO(), []
    async with ClaudeSDKClient(options=make_options()) as client:
        for words in ("Add a card for a voice commands model.", "It should be modal, with an A."):
            LAST_SENT.update(text="", at=0.0)
            out = []
            with redirect_stdout(buf):
                await client.query(talker.turn(FEED.drain(), words))
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        out.append(msg)
            said.append(heard_from(out))
            held(commands(buf.getvalue()))
            buf.seek(0), buf.truncate()
            if words.startswith("Add"):
                first = list(FEED.queued)
    merged = FEED.queued[0]["text"].lower() if len(FEED.queued) == 1 else ""
    ok(len(first) == 1 and len(FEED.queued) == 1 and "card" in merged and "modal" in merged,
       f"busy: a request and its correction are one waiting prompt ({[q['text'][:70] for q in FEED.queued]})", said[-1])


async def main():
    await offline()
    await by_voice()
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
