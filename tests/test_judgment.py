"""Opinions go to the session (asks Haiku): asking what it recommends, which option is best or what it thinks
goes to the session, even when its last answer leans one way, because you want the stronger model's view, never
voice's. Questions that help you understand what it did or wrote are answered by voice."""

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

results = []
ANSWER = ("I added a retry to the upload step and released 0.24.0. There are two ways to handle the timeout: "
          "raise it to thirty seconds, or split the upload into chunks. Chunks are more work but survive slow links.")


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
    for asked, judgment in [("Which of the two would you recommend?", True),
                            ("What do you think, is chunking worth it?", True),
                            ("Which option is best here?", True),
                            ("What did you mean by surviving slow links?", False),
                            ("What were the two options again?", False),
                            ("Did it get released?", False)]:
        FEED.pending, FEED.queued, FEED.busy, FEED.last_answer = [], [], False, ""
        FEED.add({"type": "turn_complete", "answer": ANSWER, "durationMs": 60000})
        async with ClaudeSDKClient(options=make_options()) as c:
            await turn(c, "")  # the turn's end announced
            said, sent = await turn(c, asked)
        if judgment:
            ok(len(sent) == 1, f"{asked!r} goes to the session ({sent[0]['text'] if sent else 'nothing sent'!r})", said)
        else:
            ok(not sent and bool(said), f"{asked!r} is answered by voice, nothing sent", said)
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
