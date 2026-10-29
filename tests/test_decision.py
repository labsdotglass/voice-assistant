"""When the session's turn ends with a question, voice reads it and sends nothing back until
the user clearly approves, picks or dismisses it (asks Haiku)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient

import talker
from talker import FEED, heard_from, make_options

ASKS = {"type": "turn_complete", "durationMs": 40000, "isAborted": False,
        "answer": "Early start is built behind a switch that's off. Should I turn it on now so you can compare by ear, or leave it off?"}
# (what you say after hearing the question, whether something may go to the session)
CASES = [
    ("", False),
    ("Hmm, interesting, let me think about that.", False),
    ("Not sure yet.", False),
    ("Yes, turn it on.", True),
    ("No, leave it off for now.", True),
]


async def main():
    results = []
    for said, may_send in CASES:
        FEED.pending, FEED.queued, FEED.busy = [], [], False
        FEED.add(ASKS)
        buf, msgs = io.StringIO(), []
        async with ClaudeSDKClient(options=make_options()) as client:
            with redirect_stdout(buf):
                if said:  # it reads the question first, then you answer
                    await client.query(talker.turn(FEED.drain()))
                    async for msg in client.receive_response():
                        pass
                await client.query(talker.turn(FEED.drain(), said))
                async for msg in client.receive_response():
                    msgs.append(msg)
        sent = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ") and '"submit"' in l]
        ok = bool(sent) == may_send if said else not sent and "?" in heard_from(msgs)
        results.append(ok)
        label = said or "(the session asks)"
        print(f"{'PASS' if ok else 'FAIL'}  {label!r}: {'sent ' + repr(sent[-1]['text']) if sent else 'nothing sent'}\n        heard: {heard_from(msgs)!r}")
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
