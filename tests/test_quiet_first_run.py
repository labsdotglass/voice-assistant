"""Voice stays quiet until the first run is over (no model): while it holds the greeting, what you say isn't
answered or sent to the session but goes to the first run's own listener, whose one tool steps the tour or,
on the Done screen only, presses Start talking, once."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import sys
import tempfile
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

from pipecat.frames.frames import LLMContextFrame, LLMTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

import first_run
import talker
from talker import FEED, FIRST_RUN, GREETING_RELEASED, MENU, first_run_step

first_run.STATE = Path(tempfile.mkdtemp()) / "first-run.json"  # not your real first run
first_run.REQUEST = first_run.STATE.with_name("first-run-request")
results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


class Fake:
    """A stand-in model conversation: records what it's asked, answers with a few words."""

    def __init__(self):
        self.asked = []

    async def connect(self): pass

    async def disconnect(self): pass

    async def interrupt(self): pass

    async def query(self, text):
        self.asked.append(text)

    async def receive_response(self):
        from claude_agent_sdk.types import StreamEvent
        yield StreamEvent(uuid="u", session_id="s", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Sure, I'm listening. "}})
        yield StreamEvent(uuid="u", session_id="s", event={"type": "message_stop"})


async def said(t: talker.Talker, words: str):
    ctx = LLMContext()
    ctx.add_message({"role": "user", "content": words})
    with redirect_stdout(io.StringIO()):
        await t.process_frame(LLMContextFrame(context=ctx), FrameDirection.DOWNSTREAM)
        for _ in range(20):
            await asyncio.sleep(0.02)


async def text_of(result) -> str:
    return result["content"][0]["text"]


async def main():
    t, heard = talker.Talker(), []
    t._client, t._listener = Fake(), Fake()
    t.create_task = asyncio.create_task

    async def push(frame, *a):
        if isinstance(frame, LLMTextFrame):
            heard.append(frame.text)
    t.push_frame = push
    FEED.pending = []

    # Held: no reply, nothing to the talker's conversation, the words to the first run's listener.
    GREETING_RELEASED.clear()
    first_run.write("voice-done", learned=True)
    await said(t, "Okay, start talking.")
    ok(not heard and not t._client.asked, f"held: nothing said, nothing asked of the talker ({heard}, {t._client.asked})")
    ok(len(t._listener.asked) == 1 and "Okay, start talking." in t._listener.asked[0] and "Done" in t._listener.asked[0],
       f"held: the words go to the first run's listener, with where the window is ({t._listener.asked})")

    # The session's news meanwhile isn't spoken either; it waits for after the greeting.
    t.on_session_event({"type": "finished_elsewhere", "title": "Other", "text": "Done."})
    t.on_session_event({"type": "plan", "input": {"items": [{"content": "A", "status": "in_progress"}]}})
    await asyncio.sleep(0.1)
    ok(not heard and not t._client.asked, "held: the session's news isn't spoken")

    # The listener's tool: tour steps go to the tour, as voice_menu's tour_step does.
    asked = []

    async def menu(action, value):
        asked.append((action, value))
        return True, "slide 3 of 7: The orb."
    MENU["do"] = menu
    started = []

    async def start():
        started.append(1)
        GREETING_RELEASED.set()  # what pressing Start talking does, in the end
    FIRST_RUN["start"] = start
    first_run.write("ready")
    with redirect_stdout(io.StringIO()):
        out = await first_run_step.handler({"step": "next"})
    ok(asked == [("tour_step", "next")], f"a tour step reaches the tour ({asked})")
    ok("nothing" in (await text_of(out)).lower(), "and its result asks for silence")

    # Start talking only on the Done screen: not before setup is ready, not during Your voice's reading.
    for phase in ("installing", "setup", "ready", "voice"):
        first_run.write(phase)
        talker.REPLY_CALLS.clear()  # each said in a turn of its own
        with redirect_stdout(io.StringIO()):
            await first_run_step.handler({"step": "start"})
    ok(not started, "start before the Done screen (setup, the tour, Ready, Your voice) does nothing")
    first_run.write("voice-done", learned=False)
    for _ in range(2):  # said twice, in two turns
        talker.REPLY_CALLS.clear()
        with redirect_stdout(io.StringIO()):
            await first_run_step.handler({"step": "start"})
    ok(started == [1], f"on the Done screen (after Later too): Start talking, once ({started})")

    # Released: back to answering as before, the listener gone.
    heard.clear()
    await said(t, "What's the session doing?")
    ok(bool(t._client.asked and heard), "after the first run: answered as usual")
    GREETING_RELEASED.set()

    # The daemon's end of it: the window's done and voice's start release the greeting once between them.
    first_run.write("voice-done", learned=True)
    first_run.serve_requests()
    released = []
    finish = first_run.hold_until_done(lambda: released.append(1), window_open=lambda: True)
    finish()
    first_run.REQUEST.write_text("done")
    time.sleep(0.8)
    finish()
    ok(released == [1] and first_run.phase() == "done", f"Start talking said and pressed: the greeting released once ({released})")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


def test_news_before_start():
    # The inbox opens before the pipeline runs: news then (a session that finished while voice was off) must
    # wait in the feed, not crash the talker making a task it can't make yet.
    t = talker.Talker()
    FEED.pending = []
    t.on_session_event({"type": "finished_while_off", "session": "s1", "name": "full duplex"})
    ok(True, "news before the talker starts doesn't crash it")


test_news_before_start()
asyncio.run(main())
