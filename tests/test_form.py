"""Question forms by voice: the talker reads an open form out and answers it from what you say, never
queuing your answer as a new request. The first checks need no model; the rest ask Haiku."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage

import talker
from talker import FEED, answer_question, dismiss_question, heard_from, make_options

results = []
FORM = [{"question": "Which way should the orb snap?", "options": ["Edges only", "Corners only", "Both"], "multiSelect": False}]
TWO = [
    {"question": "Which voice should be the default?", "options": ["Heart", "Michael"], "multiSelect": False},
    {"question": "Which settings should the menu show?", "options": ["Voice", "Pass on my words", "Speed"], "multiSelect": True},
]


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


def commands(out: str) -> list[dict]:
    return [json.loads(l[8:]) for l in out.splitlines() if l.startswith("@@VOICE ")]


async def answered(form_id: str, questions, answers) -> tuple[dict, list[dict]]:
    FEED.forms = {form_id: questions}
    buf = io.StringIO()
    with redirect_stdout(buf):
        reply = await answer_question.handler({"ack": "", "form": form_id, "answers": json.dumps(answers)})
    return reply, commands(buf.getvalue())


def fresh(*events):
    FEED.pending, FEED.queued, FEED.forms, FEED.busy = [], [], {}, True
    for ev in events:
        FEED.add(ev)


async def turn(client, said: str) -> tuple[str, list[dict]]:
    buf, out = io.StringIO(), []
    with redirect_stdout(buf):
        await client.query(talker.turn(FEED.drain(), said))
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                out.append(msg)
    return heard_from(out), [c for c in commands(buf.getvalue()) if c["type"] in ("answer", "dismiss", "submit", "interrupt", "cancel")]


async def main():
    # The answer the session gets: option labels as the form writes them, your own words otherwise.
    _, cmds = await answered("t1", FORM, ["both"])
    ok(cmds == [{"type": "answer", "id": "t1", "answers": {"Which way should the orb snap?": "Both"}}], f"an option, matched to its label: {cmds}")
    ok("t1" not in FEED.forms, "an answered form is no longer open")
    _, cmds = await answered("t2", TWO, ["Michael", "voice, pass on my words"])
    ok(cmds and cmds[0]["answers"] == {TWO[0]["question"]: "Michael", TWO[1]["question"]: "Voice, Pass on my words"}, "two questions, one of them several picks")
    _, cmds = await answered("t3", FORM, ["whichever is closer"])
    ok(cmds and cmds[0]["answers"][FORM[0]["question"]] == "whichever is closer", "no option fits: the user's own words go through")
    reply, cmds = await answered("t4", TWO, ["Heart"])
    ok(not cmds and "t4" in FEED.forms, "one answer for two questions: nothing sent, the form stays open")
    FEED.forms = {}
    with redirect_stdout(io.StringIO()):
        reply = await answer_question.handler({"ack": "", "form": "gone", "answers": '["Both"]'})
    ok("No such question form" in reply["content"][0]["text"], "a form already answered on screen: nothing sent")

    # Through Haiku: it reads the form out, then answers it from what you say.
    fresh({"type": "turn_start", "text": "Make the orb snap."}, {"type": "question", "id": "f1", "questions": FORM})
    async with ClaudeSDKClient(options=make_options()) as client:
        said, cmds = await turn(client, "")
        low = said.lower()
        ok(not cmds and said.rstrip().endswith("?") and ("edge" in low or "corner" in low) and not any(w in low for w in ("waiting", "asking", "decision")),
           "an open form is asked straight away, as one option, and nothing is sent", said)
        said, cmds = await turn(client, "Hmm, let me think about that for a second.")
        ok(not cmds, "thinking out loud doesn't answer it", said)
        said, cmds = await turn(client, "Let's do both.")
        ok([c["type"] for c in cmds] == ["answer"] and cmds[0]["answers"] == {FORM[0]["question"]: "Both"},
           f"the choice answers the form, not a new request: {cmds}", said)

    fresh({"type": "turn_start", "text": "Set up the menu."}, {"type": "question", "id": "f2", "questions": TWO})
    async with ClaudeSDKClient(options=make_options()) as client:
        await turn(client, "")
        said, cmds = await turn(client, "Michael as the default, and show voice and speed.")
        want = {TWO[0]["question"]: "Michael", TWO[1]["question"]: "Voice, Speed"}
        ok([c["type"] for c in cmds] == ["answer"] and cmds[0]["answers"] == want, f"two questions answered in one go: {cmds}", said)

    # Dismissed out loud, as the form's close button: nothing answered, nothing sent.
    FEED.forms = {"d0": FORM}
    with redirect_stdout(io.StringIO()) as buf:
        await dismiss_question.handler({"ack": "", "form": "d0"})
    ok(commands(buf.getvalue()) == [{"type": "dismiss", "id": "d0"}] and "d0" not in FEED.forms, "dismissing closes the form for voice and tells the mod")
    for i, said_to_skip in enumerate(["Never mind, skip that question.", "Just leave it, I don't want to pick.", "Close that form without answering."]):
        fresh({"type": "turn_start", "text": "Make the orb snap."}, {"type": "question", "id": f"d{i + 1}", "questions": FORM})
        async with ClaudeSDKClient(options=make_options()) as client:
            await turn(client, "")
            said, cmds = await turn(client, said_to_skip)
        ok([c["type"] for c in cmds] == ["dismiss"] and cmds[0]["id"] == f"d{i + 1}", f"{said_to_skip!r} dismisses the form: {cmds}", said)

    # A closing question with buttons (the app's next-step box): asked after the turn's summary; a spoken
    # pick sends that button's message, as a click does; skipping sends nothing, as its X.
    BOX = {"type": "question", "id": "b1", "questions": [{"question": "Open a test form now so you can dismiss it out loud?",
                                                          "options": ["Open a test form", "Later"], "multiSelect": False}],
           "buttons": [{"label": "Open a test form", "message": "Open a test form; I'll dismiss it out loud."},
                       {"label": "Later", "message": "Not now."}]}
    DONE = {"type": "turn_complete", "durationMs": 40000, "isAborted": False,
            "answer": "You can now dismiss a question form out loud. Committed and pushed."}
    for said_now, want in [("Yeah, open a test form.", "Open a test form; I'll dismiss it out loud."), ("Let's do it later.", "Not now."), ("Skip that one.", None)]:
        fresh({"type": "turn_start", "text": "Let voice dismiss forms."}, BOX, DONE)
        async with ClaudeSDKClient(options=make_options()) as client:
            said, cmds = await turn(client, "")
            if want == "Not now.":
                ok("test form" in said.lower() or "later" in said.lower(), "the closing question is read out with the turn's end", said)
            said, cmds = await turn(client, said_now)
        if want:
            ok([c["type"] for c in cmds] == ["submit"] and cmds[0]["text"] == want, f"{said_now!r} sends that button's message: {cmds}", said)
        else:
            ok([c["type"] for c in cmds] == ["submit"] and cmds[0]["text"] == talker.SKIP_MESSAGE and "b1" not in FEED.forms,
               f"{said_now!r} sends the short skip message, so the card closes: {cmds}", said)
            FEED.add({"type": "turn_start", "text": talker.SKIP_MESSAGE})
            ok(not FEED.add({"type": "turn_complete", "durationMs": 2000, "isAborted": False, "answer": "Skipped."}),
               "the short turn the skip started isn't announced")
            ok(FEED.add(DONE), "the next finished turn is announced as usual")
    # Asked the shortest way: the most relevant option (the session's pick) as a yes-or-no question, with no
    # lead-in about the session waiting, no other options and no "your own answer"; a plain yes picks it.
    fresh({"type": "turn_start", "text": "Let voice dismiss forms."}, BOX, DONE)
    async with ClaudeSDKClient(options=make_options()) as client:
        said, _ = await turn(client, "")
        low = said.lower()
        ok(said.rstrip().endswith("?") and not any(w in low for w in ("later", "own", "waiting", "asks", "asking")) and len(said.split()) <= 25,
           "the closing question is a short yes or no on the session's pick", said)
        said, cmds = await turn(client, "Yes.")
    ok([c["type"] for c in cmds] == ["submit"] and cmds[0]["text"] == "Open a test form; I'll dismiss it out loud.",
       f"a plain yes picks the first button: {cmds}", said)

    fresh(BOX)
    FEED.add({"type": "turn_start", "text": "Open a test form; I'll dismiss it out loud."})
    ok("b1" not in FEED.forms and not FEED.buttons, "clicked on screen (the session got new input): the closing question is gone")

    # Answered on screen: then a new request goes to the session as usual.
    fresh({"type": "turn_start", "text": "Make the orb snap."}, {"type": "question", "id": "f3", "questions": FORM})
    FEED.add({"type": "question_done", "id": "f3", "answers": {FORM[0]["question"]: "Edges only"}})
    async with ClaudeSDKClient(options=make_options()) as client:
        said, cmds = await turn(client, "Also have it update the README afterwards.")
        ok([c["type"] for c in cmds] == ["submit"], f"after an on-screen answer, a new request is sent as usual: {cmds}", said)

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
