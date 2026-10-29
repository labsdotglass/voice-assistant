"""Scripted check of the talker's routing (no mic, no TTS): answer / send / interrupt / announce."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, TextBlock

import talker
from talker import FEED, heard_from, make_options

# (session events to deliver first, what the user says, expected command type or None)
SCRIPT = [
    ([], "Hey, how's it going?", None),
    ([], "Can you ask it to add a short README to the project?", "submit"),
    (
        [{"type": "turn_start", "text": "Add a short README to the project."},
         {"type": "tool", "tool": "Bash", "summary": "ls -la"},
         {"type": "tool", "tool": "Write", "summary": "README.md"}],
        "What's it doing right now?",
        None,
    ),
    ([], "Oh, and after that, also update the changelog.", "submit"),
    ([], "Actually stop it, stop. Make it write the docs in the wiki instead.", None),  # it asks whether you're sure
    ([], "Yes, I'm sure.", "interrupt"),
    ([], "From now on, talk to me a bit slower and skip the small talk.", "submit"),
    ([{"type": "turn_complete", "answer": "I added README.md with setup and usage sections. Should I also add a license file?", "durationMs": 42000, "isAborted": False}], "", None),
]


async def main():
    failures = 0
    async with ClaudeSDKClient(options=make_options()) as client:
        for events, said, expected in SCRIPT:
            for ev in events:
                FEED.add(ev)
            prompt = FEED.drain() + said
            buf = io.StringIO()
            spoken = []
            with redirect_stdout(buf):
                await client.query(prompt)
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        spoken.append(msg)  # all of the reply: heard_from speaks it as the live path does
            cmds = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
            got = cmds[0]["type"] if cmds else None
            ok = got == expected
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  user: {said or '(session finished)'!r}")
            for c in cmds:
                print(f"        -> {c}")
            print(f"        talker: {heard_from(spoken)!r}")
            # Mirror what the mod would report back after a submit.
            if got == "submit" and not FEED.busy:
                FEED.busy = True
    print(f"\n{len(SCRIPT) - failures}/{len(SCRIPT)} as expected")
    sys.exit(1 if failures else 0)


asyncio.run(main())
