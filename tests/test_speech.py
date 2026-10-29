"""The talker's streaming speech path: IDs split across chunks are still shortened whole."""
import _root  # noqa: F401  (the repo root on the import path)
import asyncio, io, sys
from contextlib import redirect_stdout
from claude_agent_sdk.types import StreamEvent
from pipecat.frames.frames import LLMTextFrame
import talker

CHUNKS = ["The commit is 3ac8", "e7f2b9", "1d and the session af81c816-5e34-", "4219-8320-e88d6f4b5dde", ". Done", " now."]

class FakeClient:
    async def query(self, text): pass
    async def receive_response(self):
        for c in CHUNKS:
            yield StreamEvent(uuid="u", session_id="s", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": c}})
        yield StreamEvent(uuid="u", session_id="s", event={"type": "message_stop"})

async def main():
    t = talker.Talker()
    t._client = FakeClient()
    heard = []
    async def push(frame, *a):
        if isinstance(frame, LLMTextFrame): heard.append(frame.text)
    t.push_frame = push
    buf = io.StringIO()
    with redirect_stdout(buf):
        await t._reply("what's up")
    said = "".join(heard)
    print(repr(said))
    ok = said.strip() == "The commit is 3ac8… and the session af81…. Done now." and "3ac8e7f" not in buf.getvalue().split('"talker"')[-1]
    print("PASS" if ok else "FAIL", "streamed IDs shortened whole; transcript matches speech")
    sys.exit(0 if ok else 1)
asyncio.run(main())
