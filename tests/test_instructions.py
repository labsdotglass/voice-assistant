"""How you tell voice to talk lasts, and stays with voice (asks Haiku): a style instruction is followed, kept in
~/.voice-assistant/instructions.txt and given to the talker at its next start, and never sent to the session, which
is your own project. Asked to forget them, voice clears them."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

import talker
from talker import FEED, heard_from, make_options

talker.INSTRUCTIONS = Path(tempfile.mkdtemp()) / "instructions.txt"  # not your real instructions
results = []


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


async def main():
    ok(talker.instructions_rule() == "", "nothing saved: nothing added to the prompt")
    FEED.pending, FEED.queued, FEED.busy, FEED.last_answer = [], [], False, ""
    async with ClaudeSDKClient(options=make_options()) as c:
        said, calls, sent = await turn(c, "From now on, keep your answers to one short sentence.")
        ok("remember_instruction" in calls and not sent, f"a style instruction is kept, and nothing goes to the session ({calls})", said)
        saved = talker.saved_instructions()
        ok(len(saved) == 1 and "sentence" in saved[0].lower(), f"it's saved in your words ({saved})")
        said, calls, sent = await turn(c, "Switch to Michael's voice.")
        ok("remember_instruction" not in calls and not sent, f"a voice setting isn't kept as an instruction ({calls})", said)

    ok(saved and saved[0] in talker.system_prompt(), "the next start gives the talker what was saved")

    async with ClaudeSDKClient(options=make_options()) as c:
        said, calls, sent = await turn(c, "Forget what I told you about how to talk.")
        ok("forget_instructions" in calls and not sent and not talker.saved_instructions(),
           f"asked to forget: the saved instructions are gone ({calls})", said)
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
