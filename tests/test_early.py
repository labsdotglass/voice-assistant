"""Early start (experiment): with the switch on, a reply's first clause goes to the voice before its
first sentence ends; with it off, nothing changes (no models)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from claude_agent_sdk.types import StreamEvent
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMTextFrame

import talker
from talker import FEED

talker.EARLY_START = Path(tempfile.mkdtemp()) / "early-start"  # not your real switch
results = []


def ok(cond, msg, detail=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + msg + (f"\n        {detail}" if detail else ""))


def fake(chunks):
    class Fake:
        async def query(self, text):
            pass

        async def receive_response(self):
            for c in chunks:
                yield StreamEvent(uuid="u", session_id="s", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": c}})
            yield StreamEvent(uuid="u", session_id="s", event={"type": "message_stop"})

    return Fake()


async def speak(chunks):
    """The pieces the voice gets: text between response ends (each piece is spoken as it closes)."""
    t, pieces = talker.Talker(), [[]]
    t._client = fake(chunks)

    async def push(frame, *a):
        if isinstance(frame, LLMTextFrame):
            pieces[-1].append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            pieces.append([])

    t.push_frame = push
    FEED.pending = ["[session finished]"]
    with redirect_stdout(io.StringIO()):
        await t._reply("")
    return ["".join(p).strip() for p in pieces if "".join(p).strip()]


async def main():
    long_opener = ["The badge now sits on ", "the orb's top-right edge, and ", "voice has restarted. ", "Two tasks are queued."]
    off = await speak(long_opener)
    ok(len(off) == 1, "switch off: spoken as before, the first sentence waits for its end", off)

    talker.EARLY_START.touch()
    on = await speak(long_opener)
    ok(on[:1] == ["The badge now sits on the orb's top-right edge,"] and "".join(on).replace(" ", "") == "".join(off).replace(" ", ""),
       "switch on: the first clause is its own piece, spoken at once; nothing lost", on)
    short = await speak(["Done, ", "it's pushed. ", "Voice has restarted, too."])
    ok(len(short) == 1, "a short opener (comma after fewer than 4 words): left alone", short)
    talker.EARLY_START.unlink()

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
