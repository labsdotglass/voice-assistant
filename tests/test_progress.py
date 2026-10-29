"""Progress while the session works: voice speaks how its plan is going (a step changed), briefly,
throttled and switchable; what the session writes in between is background, never announced."""

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
from talker import FEED, heard_from, make_options

talker.PROGRESS_OFF = Path(tempfile.mkdtemp()) / "progress-off"  # not your real switch
results = []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


def note(text):
    return {"type": "progress", "text": text}


PLAN_ITEMS = [{"content": "Read the stop path", "status": "in_progress"}, {"content": "Fix it", "status": "pending"},
              {"content": "Run the checks", "status": "pending"}]


def plan(**inp):
    return {"type": "plan", "input": inp}


async def main():
    FEED.pending, FEED.last_progress = [], 0.0
    ok(not FEED.add(note("Reading the daemon's stop path.")), "what the session writes isn't announced")
    ok(any("Reading the daemon" in line for line in FEED.pending), "but it's kept, to answer questions with")
    ok(FEED.add(plan(items=PLAN_ITEMS)), "a new plan is announced (step 1 of 3)")
    FEED.last_progress -= 9
    ok(FEED.add(plan(advance=True)) and "step 2 of 3" in FEED.pending[-1], "moving to the next step is announced", FEED.pending[-1])
    ok(not FEED.add(plan(advance=True)), "a step change right after another waits (not spoken)")
    FEED.last_progress -= 9
    ok(not FEED.add(plan(advance=True)), "the plan's last step closing isn't announced: the task's end is")
    talker.PROGRESS_OFF.touch()
    FEED.last_progress = 0.0
    ok(not FEED.add(plan(items=PLAN_ITEMS)), "switched off (progress-off): never spoken")
    talker.PROGRESS_OFF.unlink()

    # Arriving mid-task: the plan is rebuilt from the updates that made it, kept as background, never announced.
    talker.PLAN.steps, FEED.pending = [], []
    arrived = {"type": "plan", "arrived": True, "updates": [{"items": PLAN_ITEMS}, {"advance": True}]}
    ok(not FEED.add(arrived) and "step 2 of 3" in FEED.pending[-1] and "Run the checks (pending)" in FEED.pending[-1],
       "arriving mid-task: the plan so far is known, step by step, and not announced", FEED.pending[-1] if FEED.pending else None)
    talker.PLAN.steps, FEED.pending = [], []
    done = {"type": "plan", "arrived": True, "updates": [{"items": PLAN_ITEMS}, {"done": [1, 2, 3]}]}
    ok(not FEED.add(done) and not FEED.pending, "a finished plan on arrival adds nothing")

    from talker import clip_answer
    long = "Opening: what I changed. " + "detail " * 400 + "Question for you: ship it?"
    clipped = clip_answer(long)
    ok(len(clipped) < 1300 and clipped.startswith("Opening") and clipped.endswith("ship it?"),
       f"a long session answer reaches the talker cut in the middle ({len(long)} -> {len(clipped)} characters), keeping its ending")

    # The cap, through the talker's real speech path: a progress update stops at its first sentence.
    from claude_agent_sdk.types import StreamEvent
    from pipecat.frames.frames import LLMTextFrame

    class Fake:
        async def query(self, text): pass

        async def receive_response(self):
            # A chunk that ends one sentence and starts the next, as the live speech path cuts them.
            for c in ["The session found the cause ", "and is fixing it. It also ", "plans to ", "run the checks after."]:
                yield StreamEvent(uuid="u", session_id="s", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": c}})
            yield StreamEvent(uuid="u", session_id="s", event={"type": "message_stop"})

    for one, want in [(True, "The session found the cause and is fixing it."), (False, "The session found the cause and is fixing it. It also plans to run the checks after.")]:
        t, heard = talker.Talker(), []
        t._client = Fake()

        async def push(frame, *a):
            if isinstance(frame, LLMTextFrame):
                heard.append(frame.text)
        t.push_frame = push
        FEED.pending = ["[session progress note: the session is fixing it]"]
        with redirect_stdout(io.StringIO()):
            await t._reply("", one_sentence=one)
        ok("".join(heard).strip() == want, f"{'a progress update stops at its first sentence' if one else 'other replies are spoken whole'}", "".join(heard).strip())

    cases = [  # (the plan update, then what the session wrote alongside it)
        (dict(items=PLAN_ITEMS), "Starting with the daemon's stop path, then the mod's turnOff."),
        (dict(advance=True), "Found the cause: marking voice off writes undefined into the panel's state, which the app "
                             "refuses, so the stop never reaches the daemon. Fixing the mod to store null and to stop the daemon first."),
    ]
    talker.PLAN.steps = []
    for update, text in cases:
        FEED.pending, FEED.queued, FEED.busy, FEED.last_progress = [], [], True, 0.0
        FEED.add({"type": "turn_start", "text": "Fix the orb's Stop voice."})
        FEED.add(note(text))
        FEED.add(plan(**update))
        buf, out = io.StringIO(), []
        async with ClaudeSDKClient(options=make_options()) as client:
            with redirect_stdout(buf):
                await client.query(talker.turn(FEED.drain()))
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        out.append(msg)  # all of the reply: heard_from speaks it as the live path does
        said = re.split(r"(?<=[.!?])\s", heard_from(out))[0]  # what's spoken: the first sentence (the cap)
        cmds = [json.loads(l[8:])["type"] for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
        n_words = len(re.findall(r"[\w']+", said))
        ok(0 < n_words <= 20 and "?" not in said and not {"submit", "interrupt", "cancel"} & set(cmds),
           f"a few spoken words on how the plan is going ({n_words} words), no question, nothing sent", said)

    # Asked for the status, it answers from everything it has seen, not just the plan step.
    FEED.pending, FEED.queued, FEED.busy, FEED.last_progress = [], [], True, 0.0
    talker.PLAN.steps = []
    FEED.add({"type": "turn_start", "text": "Fix the orb's Stop voice."})
    FEED.add(plan(items=PLAN_ITEMS))
    FEED.add(plan(advance=True))
    FEED.add(note("Found it: the stop never reached the daemon. Running the type check on the fix now."))
    out = []
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(io.StringIO()):
            await client.query(FEED.drain() + "What's the status?")
            async for msg in client.receive_response():
                out.append(msg)
    said = heard_from(out)
    low = said.lower()
    ok(("type check" in low or "fix" in low or "found" in low) and len(said.split()) >= 6,
       "asked for the status: it answers from what the session is doing, not only the plan step", said)

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
