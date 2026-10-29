"""Voice speaks a tool's ack as the call streams, so you hear it without waiting for the
tool and a second model round trip, and it doesn't repeat itself after (asks Haiku)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import statistics
import sys
import time
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import StreamEvent

import talker
from talker import ACK, FEED, heard_from, make_options

CASES = [
    ("idle session, a request", [], "Ask it to run the tests."),
    ("busy session, a request", [{"type": "turn_start", "text": "Refactor the focus watcher."}], "After that, have it update the README."),
    ("busy session, stop it", [{"type": "turn_start", "text": "Refactor the focus watcher."}], "Stop what it's doing right now."),
    ("a goodbye", [{"type": "turn_complete", "answer": "Done.", "durationMs": 1000, "isAborted": False}], "That's all for today, bye."),
]


async def main():
    results, ack_times, reply_times = [], [], []
    for name, events, said in CASES:
        FEED.pending, FEED.queued, FEED.busy = [], [], False
        for ev in events:
            FEED.add(ev)
        tool_json, ack_at, done_at, messages = "", None, None, []
        async with ClaudeSDKClient(options=make_options()) as client:
            t0 = time.perf_counter()
            with redirect_stdout(io.StringIO()):
                await client.query(talker.turn(FEED.drain(), said))
                async for msg in client.receive_response():
                    messages.append(msg)
                    if not isinstance(msg, StreamEvent):
                        continue
                    ev = msg.event
                    if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "input_json_delta":
                        tool_json += ev["delta"].get("partial_json", "")
                        if ack_at is None and ACK.search(tool_json):
                            ack_at = time.perf_counter() - t0
            done_at = time.perf_counter() - t0
        heard = heard_from(messages)
        ack = json.loads(f'"{ACK.search(tool_json).group(1)}"') if ack_at else ""
        ok = bool(ack) and heard.count(ack) == 1 and len(heard) < len(ack) + 40
        results.append(ok)
        if ack_at:
            ack_times.append(ack_at)
        reply_times.append(done_at)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: ack heard at {ack_at or 0:.1f}s (reply done at {done_at:.1f}s)\n        heard: {heard!r}")
    print(f"ack heard at: median {statistics.median(ack_times or [0]):.1f}s; the whole reply took median {statistics.median(reply_times):.1f}s")
    print(f"{sum(results)}/{len(results)} passed (at least {len(results) - 1} needed)")
    sys.exit(0 if sum(results) >= len(results) - 1 else 1)


asyncio.run(main())
