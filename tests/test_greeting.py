"""The talker greets in its own words and never repeats itself (asks Haiku; no mic, no TTS)."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, TextBlock

import talker
from talker import FEED, greeting_note, heard_from, make_options, remember_greeting

talker.GREETINGS = Path(tempfile.mkdtemp()) / "greetings.json"  # not your real greeting history
results = []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def overlap(a: str, b: str) -> float:
    """How much two sentences share: 1 = the same words."""
    wa, wb = words(a), words(b)
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


async def say(client, text: str) -> str:
    out = []
    with redirect_stdout(io.StringIO()):
        await client.query(talker.turn(FEED.drain(), text))
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                out.append(msg)  # all of the reply: heard_from speaks it as the live path does
    return heard_from(out)


async def main():
    greetings = []
    for _ in range(3):  # three start-ups in a row, each a fresh talker
        FEED.pending, FEED.queued = [greeting_note()], []
        async with ClaudeSDKClient(options=make_options()) as client:
            g = await say(client, "")
        remember_greeting(g)
        greetings.append(g)
        ok(0 < len(g.split()) <= 25, "a short greeting", g)
    pairs = [(a, b) for i, a in enumerate(greetings) for b in greetings[i + 1:]]
    ok(all(overlap(a, b) < 0.7 for a, b in pairs), f"three start-ups, three different greetings (most alike: {max(overlap(a, b) for a, b in pairs):.2f})")
    ok(json.loads(talker.GREETINGS.read_text()) == greetings, "each greeting is remembered for the next start-up")

    # First start (onboarding): the greeting asks you to read the phrases in the window that opens, and
    # sends nothing: Haiku once sent its cue to the session as your prompt. Several tries, as it did so rarely.
    talker.ENROLLING["on"] = True
    talker.GREETING["on"] = True  # as Talker._greet sets it
    for _ in range(4):
        FEED.pending, FEED.queued = [greeting_note()], []
        buf = io.StringIO()
        async with ClaudeSDKClient(options=make_options()) as client:
            with redirect_stdout(buf):
                await client.query(talker.turn(FEED.drain()))
                out = [m async for m in client.receive_response() if isinstance(m, AssistantMessage)]
        g = heard_from(out)
        sent = [l for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ") and '"submit"' in l]
        ok(not sent and "read" in g.lower() and ("window" in g.lower() or "phrase" in g.lower()) and len(g.split()) <= 45,
           "on a first start, the greeting asks you to read the phrases in the window, and sends nothing", g)
    talker.ENROLLING["on"] = False
    talker.GREETING["on"] = False

    # The first run's greeting, said once they press Start talking (the tour is behind them by then).
    talker.FIRST_TOUR["on"], talker.GREETING["on"] = True, True
    FEED.pending, FEED.queued = [greeting_note()], []
    buf = io.StringIO()
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(buf):
            await client.query(talker.turn(FEED.drain()))
            out = [m async for m in client.receive_response() if isinstance(m, AssistantMessage)]
    g = heard_from(out)
    talker.FIRST_TOUR["on"], talker.GREETING["on"] = False, False
    ok("tour" not in g.lower() and not [l for l in buf.getvalue().splitlines() if '"submit"' in l] and len(g.split()) <= 50,
       "after the first run's Start talking, the greeting welcomes them without the tour, and sends nothing", g)

    FEED.pending, FEED.queued, FEED.busy = [], [], False
    async with ClaudeSDKClient(options=make_options()) as client:
        said = await say(client, "From now on, talk to me a bit slower and skip the small talk.")
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", said) if s.strip()]
        twice = [(a, b) for i, a in enumerate(sentences) for b in sentences[i + 1:] if overlap(a, b) >= 0.6]
        ok(not twice, "a reply with a tool call makes its point once", said)

        acks = []
        FEED.busy = True
        for ask in ["Have it update the README.", "Also have it bump the changelog.", "And have it tag a release."]:
            acks.append(await say(client, ask))
        pairs = [(a, b) for i, a in enumerate(acks) for b in acks[i + 1:]]
        ok(all(a.lower() != b.lower() and overlap(a, b) < 0.7 for a, b in pairs), "three requests, three differently worded acknowledgments", " | ".join(acks))

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
