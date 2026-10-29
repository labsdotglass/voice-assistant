"""Pass on my words (asks Haiku): Concise sends your request in few words; Cleaned up sends your own wording
minus the fillers and false starts, keeping every detail, and a switch while voice runs takes effect."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import re
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient

import talker
from talker import FEED, make_options

SAID = ("Um, so, can you, uh, have it look at the, the orb menu, and like, add a small gap above the Stop voice item, "
        "and also make the font a bit smaller, maybe twelve points, yeah.")
FILLERS = ["um", "uh", "like", "yeah", "the the"]
DETAILS = ["gap", "stop voice", "font", "twelve|12"]
results = []


def ok(cond, msg, prompt):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}\n        -> session: {prompt!r}")


def n_words(text):
    return len(re.findall(r"[\w']+", text))


async def sent(client, said: str) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        note, talker.PASS_ON["note"] = talker.PASS_ON["note"], ""  # as Talker._reply sends it
        await client.query(note + FEED.drain() + said)
        async for _ in client.receive_response():
            pass
    cmds = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
    return " ".join(c["text"] for c in cmds if c["type"] == "submit")


def clean(prompt: str) -> bool:
    low = " " + re.sub(r"[^\w' ]", " ", prompt.lower()) + " "
    return not any(f" {f} " in low for f in FILLERS)


def kept(prompt: str) -> bool:
    return all(re.search(d, prompt.lower()) for d in DETAILS)


async def main():
    FEED.pending, FEED.queued, FEED.busy = [], [], False
    talker.PASS_ON.update(mode="concise", note="")
    async with ClaudeSDKClient(options=make_options()) as client:
        concise = await sent(client, SAID)
    ok(clean(concise) and n_words(concise) < n_words(SAID) * 0.85, "Concise: few words, no fillers", concise)

    talker.PASS_ON.update(mode="cleaned", note="")
    async with ClaudeSDKClient(options=make_options()) as client:
        cleaned = await sent(client, SAID)
    ok(clean(cleaned) and kept(cleaned) and n_words(cleaned) > n_words(concise), "Cleaned up: your wording, no fillers, every detail, longer than Concise", cleaned)

    # Switched while voice runs: the talker connected as Concise, then you pick Cleaned up.
    talker.PASS_ON.update(mode="concise", note="")
    async with ClaudeSDKClient(options=make_options()) as client:
        await sent(client, "Have it run the tests.")
        talker.set_pass_on("cleaned")
        switched = await sent(client, SAID)
    talker.PASS_ON.update(mode="concise", note="")
    ok(clean(switched) and kept(switched), "switched to Cleaned up mid-conversation: it takes effect", switched)

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
