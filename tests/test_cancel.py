"""Targeted check: the talker takes back queued prompts by voice, and only those."""
import _root  # noqa: F401  (the repo root on the import path)
import asyncio, io, json, sys
from contextlib import redirect_stdout
from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, TextBlock
import talker
from talker import FEED, heard_from, make_options

fails = 0

async def ask(client, text):
    buf, out = io.StringIO(), []
    with redirect_stdout(buf):
        await client.query(talker.turn(FEED.drain(), text))
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                out.append(msg)  # all of the reply: heard_from speaks it as the live path does
    cmds = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
    return heard_from(out), [c for c in cmds if c["type"] in ("submit", "cancel", "interrupt")]

def check(name, ok, said, cmds):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n        heard: {said!r}\n        cmds: {cmds}")

async def main():
    async with ClaudeSDKClient(options=make_options()) as client:
        FEED.add({"type": "turn_start", "text": "Refactor the focus watcher."})
        held = []  # what the mod holds, built from the submits it gets, as the real mod does
        said, cmds = await ask(client, "After that, ask it to update the README.")
        held += [{"id": c["id"], "text": c["text"]} for c in cmds if c["type"] == "submit"]
        check("queues the README prompt", [c["type"] for c in cmds] == ["submit"], said, cmds)
        said, cmds = await ask(client, "And then also have it bump the changelog.")
        held += [{"id": c["id"], "text": c["text"]} for c in cmds if c["type"] == "submit"]
        check("queues the changelog prompt", [c["type"] for c in cmds] == ["submit"], said, cmds)
        ids = [q["id"] for q in held]
        FEED.add({"type": "queue", "items": held})  # the mod's own list, as it posts it

        said, cmds = await ask(client, "Actually, never mind the changelog one.")
        check("cancels just the changelog prompt", cmds == [{"type": "cancel", "ids": [ids[1]]}] and len(FEED.queued) == 1, said, cmds)
        said, cmds = await ask(client, "Hmm, drop whatever's still waiting too.")
        check("cancels the rest", len(cmds) == 1 and cmds[0]["type"] == "cancel" and not FEED.queued, said, cmds)
        said, cmds = await ask(client, "And stop what it's doing right now.")
        check("running work: it asks whether you're sure before stopping it", not cmds and "?" in said, said, cmds)
        said, cmds = await ask(client, "Yes.")
        check("running work is an interrupt, not a cancel", [c["type"] for c in cmds] == ["interrupt"], said, cmds)
        said, cmds = await ask(client, "Cancel the README thing.")
        check("nothing waiting: no cancel command goes out", not any(c["type"] == "cancel" for c in cmds), said, cmds)
    print(f"{6 - fails}/6")
    sys.exit(1 if fails else 0)

asyncio.run(main())
