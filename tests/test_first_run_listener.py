"""The first run's listener (asks Haiku): while voice stays quiet until Start talking, saying it on the Done screen
presses it, in any of voice's languages; the tour steps by voice; nothing else does anything, and Start talking
never fires before the Done screen."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import first_run
import talker
from talker import FIRST_RUN, GREETING_RELEASED, MENU, first_run_turn, heard_from, make_options

first_run.STATE = Path(tempfile.mkdtemp()) / "first-run.json"  # not your real first run
results = []
asked, started = [], []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        wrote: {said!r}" if said else ""))


async def step(action, value):
    asked.append(value.strip().lower())
    return True, "slide 3 of 7: The orb."


async def start():
    started.append(1)


async def hear(phase: str, text: str) -> str:
    first_run.write(phase, **({"learned": True} if phase == "voice-done" else {}))
    asked.clear()
    started.clear()
    FIRST_RUN["start"] = start
    out = []
    async with ClaudeSDKClient(options=make_options(listener=True)) as client:
        with redirect_stdout(io.StringIO()):
            await client.query(first_run_turn(text))
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    out.append(msg)
    return heard_from(out)


async def main():
    GREETING_RELEASED.clear()
    MENU["do"] = step

    # Start talking said on the Done screen, in each of voice's languages.
    for text in ["Okay, start talking.", "Alright, I'm ready, let's go.", "בוא נתחיל לדבר.", "Vale, empecemos a hablar.",
                 "D'accord, on commence à parler.", "Ok, iniziamo a parlare.", "Certo, vamos começar a falar.",
                 "ठीक है, बात करना शुरू करते हैं।"]:
        wrote = await hear("voice-done", text)
        ok(started == [1] and not asked, f"Done screen: {text!r} presses Start talking (started {started}, steps {asked})", wrote)

    # On the Done screen, what isn't about starting does nothing.
    for text in ["Hmm, what does this screen say?", "Hang on, not yet.", "Can you fix the login bug?"]:
        wrote = await hear("voice-done", text)
        ok(not started and not asked, f"Done screen: {text!r} does nothing (started {started}, steps {asked})", wrote)

    # Before the Done screen, Start talking never fires: setup, the tour, Your voice's reading.
    for phase, text in [("setup", "Okay, start talking."), ("ready", "Let's start talking."), ("voice", "Okay, start talking."),
                        ("voice", "Can you show me what changed since yesterday?")]:
        wrote = await hear(phase, text)
        ok(not started, f"{phase}: {text!r} doesn't start voice (started {started}, steps {asked})", wrote)

    # The tour still steps by voice.
    for phase, text, value in [("setup", "Next.", "next"), ("ready", "Go to step three.", "3"), ("ready", "Go back.", "back")]:
        wrote = await hear(phase, text)
        ok(asked == [value] and not started, f"{phase}: {text!r} steps the tour to {value} (steps {asked})", wrote)

    GREETING_RELEASED.set()
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
