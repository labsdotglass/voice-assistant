"""The talker closes voice only on an explicit goodbye (asks Haiku; no mic, no TTS).

Each case is a fresh conversation: an explicit goodbye calls end_voice; thanks alone gets a
question instead; thanks with more work goes on; queued prompts are mentioned before closing.
"""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, TextBlock

import talker
from talker import ENDING, FEED, heard_from, make_options

DONE = {"type": "turn_complete", "durationMs": 30000, "isAborted": False,
        "answer": "I fixed the orb's Stop voice and pushed it; voice now stops from the orb."}
results = []


def ok(cond, msg, said, cmds):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}\n        heard: {said!r}" + (f"\n        cmds: {cmds}" if cmds else ""))


async def turn(events: list[dict], said: str):
    """One fresh conversation: the session's news, then what you said. -> (spoken, commands, closed?)"""
    FEED.pending, FEED.queued, FEED.busy = [], [], False
    ENDING.cancel_quietly()
    for ev in events:
        FEED.add(ev)
    buf, out = io.StringIO(), []
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(buf):
            await client.query(talker.turn(FEED.drain(), said))
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    out.append(msg)  # all of the reply: heard_from speaks it as the live path does
    cmds = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
    cmds = [c for c in cmds if c["type"] in ("submit", "interrupt", "cancel")]
    return heard_from(out), cmds, ENDING.requested


async def main():
    said, cmds, closed = await turn([DONE], "Bye!")
    ok(closed, "an explicit goodbye closes voice", said, cmds)

    said, cmds, closed = await turn([DONE], "That's all, thanks, we're done.")
    ok(closed, "\"that's all, we're done\" closes voice", said, cmds)

    said, cmds, closed = await turn([DONE], "Thanks.")
    ok(not closed and "?" in said, "thanks alone doesn't close; it asks if there's anything else", said, cmds)

    said, cmds, closed = await turn([DONE], "Thanks, and can you also have it check the logs?")
    ok(not closed and any(c["type"] == "submit" for c in cmds), "thanks with more work goes on", said, cmds)

    waiting = {"type": "queue", "items": [{"id": "q1", "text": "Update the README"}, {"id": "q2", "text": "Bump the changelog"}]}
    said, cmds, closed = await turn([{"type": "turn_start", "text": "Refactor the focus watcher."}, waiting], "Okay, that's it for now, bye.")
    words = said.lower()
    ok(closed and any(w in words for w in ("waiting", "queued", "still", "two", "2 ")), "queued prompts are mentioned before closing", said, cmds)

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
