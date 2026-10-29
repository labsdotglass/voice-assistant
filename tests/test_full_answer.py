"""Long answers by voice (asks Haiku): what the talker is shown of a long session answer is cut in the middle,
so when you ask for more it reads the whole answer (read_full_answer) and goes through it in short parts, asking
whether to go on, and continues from there without fetching it again. And the arrival history keeps each long
message's end, with the last reply in full."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import re
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

import talker
from talker import FEED, clip_answer, heard_from, make_options

results = []
# A long answer whose middle (what clip_answer cuts) holds the detail you ask about.
MIDDLE = ("The history keeps your messages and the session's replies. Tool calls and tags are left out. Your messages "
          "are cut at four hundred characters and the session's at seven hundred. The newest are kept first, up to six "
          "thousand characters, then put back in order. ")
ANSWER = ("I fixed the plan gap and released it. The mod keeps the plan's updates and sends them again when voice "
          "arrives mid-task. " + "Some filler about the checks that ran. " * 8 + "Here is how the recent chat context "
          "works. " + MIDDLE * 3 + "Some filler about versions and restarts. " * 25 + "Want me to clip the history "
          "the same way?")


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
    calls = [b.name.split("__")[-1] for m in out for b in m.content if isinstance(b, ToolUseBlock)]
    sent = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ") and '"submit"' in l]
    return heard_from(out), calls, sent


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


async def main():
    clipped = clip_answer(ANSWER)
    ok("six thousand" not in clipped and "read_full_answer" in clipped, "the talker's copy of a long answer is cut, and says where")

    FEED.pending, FEED.queued, FEED.busy, FEED.last_answer = [], [], False, ""
    FEED.add({"type": "turn_complete", "answer": ANSWER, "durationMs": 90000})
    ok(FEED.last_answer == ANSWER, "the full answer is kept for read_full_answer")
    async with ClaudeSDKClient(options=make_options()) as c:
        said, _, _ = await turn(c, "")  # the turn's end announced, from the clipped answer
        said, calls, sent = await turn(c, "Tell me more about how the chat history works.")
        ok("read_full_answer" in calls and not sent, f"asked for more: it reads the full answer, sends nothing ({calls})", said)
        ok(len(sentences(said)) <= 5 and said.rstrip().endswith("?"), "it gives one short part, then asks whether to go on", said)
        said, calls, sent = await turn(c, "Yes, go on.")
        ok("read_full_answer" not in calls and not sent and bool(said), f"'go on': it continues without fetching it again ({calls})", said)

    # The arrival history brings the session's last reply in full too; a switch drops the previous one.
    FEED.add({"type": "switched", "title": "Other", "session": "s2", "project": "p"})
    ok(FEED.last_answer == "", "a switch drops the previous session's answer")
    FEED.add({"type": "history", "messages": [{"role": "assistant", "text": clipped}], "answer": ANSWER})
    ok(FEED.last_answer == ANSWER, "the arrival history's full reply is kept for read_full_answer")
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
