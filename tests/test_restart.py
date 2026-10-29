"""Voice restarting carries on the conversation instead of greeting afresh: the restart reason (the mod's
--restart, the daemon's own marker), the conversation from before it given back, and what the talker says
(asks Haiku): a few words that it's back after a reload, a follow-up that leans on what was said, a long answer continued
from its next part, and a few words in the new language after a language switch."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import json
import re
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

import talker
from talker import FEED, RESTART, greeting_note, heard_from, make_options, mark_restart, restart_note, restart_reason

talker.RESTART_MARKER = Path(tempfile.mkdtemp()) / "restart-reason"  # not the live daemon's
talker.GREETINGS = Path(tempfile.mkdtemp()) / "greetings.json"
results = []


def ok(cond, msg, said=None):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}" + (f"\n        heard: {said!r}" if said is not None else ""))


def restart(why: str, language: str = ""):
    RESTART.update(why=why, language=language, restored=False)
    FEED.pending, FEED.queued, FEED.busy, FEED.last_answer = [], [], False, ""


async def turn(client, text):
    buf, out = io.StringIO(), []
    with redirect_stdout(buf):
        await client.query(talker.turn(FEED.drain(), text))
        async for m in client.receive_response():
            if isinstance(m, AssistantMessage):
                out.append(m)
    calls = [b.name.split("__")[-1] for m in out for b in m.content if isinstance(b, ToolUseBlock)]
    sent = [json.loads(l[8:]) for l in buf.getvalue().splitlines() if l.startswith("@@VOICE ")]
    return heard_from(out), calls, [s for s in sent if s.get("type") == "submit"]


def at(secs_ago: float) -> int:
    return int((time.time() - secs_ago) * 1000)


def offline():
    # The reason: the daemon's own marker (it knows it's a language switch) wins over the mod's flag, once.
    ok(restart_reason(None) == "", "no flag and no marker: a fresh start")
    ok(restart_reason("the restart file") == "the restart file", "the mod's --restart makes it a restart")
    mark_restart("the language is now he")
    ok(restart_reason("the daemon died while this session had voice") == "the language is now he", "a recent marker says why: a language switch")
    ok(restart_reason(None) == "the language is now he", "a start killed before it spoke leaves the marker for the next one")
    talker.restart_told()
    ok(restart_reason(None) == "", "once a start has said it's back, the marker is used up")
    talker.RESTART_MARKER.write_text(json.dumps({"why": "the language is now he", "at": time.time() - 600}))
    ok(restart_reason(None) == "", "a marker from ten minutes ago is another conversation: a fresh start")

    # What voice says in place of a greeting: a few words that it's back, in the new language after a switch.
    restart("the restart file")
    ok("back" in restart_note() and "greet" in restart_note(), "after a reload, voice says it's back and listening")
    restart("the language is now he", "Hebrew")
    ok("Hebrew" in restart_note() and "greet" in restart_note(), "after a language switch, voice says it's back in the new language")

    # The history after a restart: voice's own lines and the last messages are the conversation, not background.
    restart("the restart file")
    FEED.add({"type": "history", "messages": [{"role": "user", "text": f"old {i}"} for i in range(5)],
              "voice": [{"who": "you", "text": "Which tests failed?", "at": at(30)}]})
    text = "\n".join(FEED.pending)
    ok("Which tests failed?" in text and "conversation so far" in text and RESTART["restored"], "after a restart, voice's own lines come back as its conversation")
    ok(text.count("old 0") == 1 and "old 0" in FEED.pending[0] and "old 4" in FEED.pending[-1], "the session's last three messages are the context voice was working from")
    FEED.pending = []
    FEED.add({"type": "history", "messages": [{"role": "user", "text": "later"}], "voice": [{"who": "you", "text": "again", "at": at(5)}]})
    later = "\n".join(FEED.pending)
    ok("conversation so far" not in later and "last voice conversation" in later,
       "only the first history after a restart is the conversation going on (a later switch is background)")
    restart("")
    FEED.add({"type": "history", "messages": [{"role": "user", "text": "hi"}], "voice": [{"who": "you", "text": "stale", "at": at(5)}]})
    text = "\n".join(FEED.pending)
    ok("stale" in text and "last voice conversation" in text and "conversation so far" not in text,
       "on a fresh start, voice's old lines come back as background, not as the conversation going on")
    ok("never a word about this note" in greeting_note(), "the greeting never narrates its cue")


async def live():
    # A reload mid-conversation: nothing is said on its own, and a follow-up leans on what was said before it.
    restart("the restart file")
    FEED.add({"type": "history", "messages": [
        {"role": "user", "text": "Check which of the two deploy scripts is still used."},
        {"role": "assistant", "text": "deploy.sh is used by CI. deploy-old.sh isn't referenced anywhere; it was replaced in March."}],
        "voice": [
            {"who": "you", "text": "Which deploy script does CI use?", "at": at(40)},
            {"who": "talker", "text": "CI uses deploy.sh.", "at": at(38)}]})
    async with ClaudeSDKClient(options=make_options()) as client:
        said, calls, sent = await turn(client, "And the other one?")
    ok("old" in said.lower() or "isn't" in said.lower() or "not" in said.lower() or "march" in said.lower(),
       "after a restart, a follow-up leans on the conversation from before it", said)
    ok(not re.search(r"\b(hi|hey|hello|welcome)\b", said.lower()), "and voice doesn't greet afresh", said)

    # In the middle of a long answer: it continues with the next part, not from the start.
    restart("the daemon died while this session had voice")
    parts = ["First, the login page moved to the new router.", "Second, the session cookie now expires after a week.",
             "Third, the logout button clears every open tab.", "Fourth, password resets send a code instead of a link."]
    FEED.add({"type": "history", "answer": " ".join(parts), "messages": [
        {"role": "user", "text": "Summarise the auth changes."}, {"role": "assistant", "text": " ".join(parts)}],
        "voice": [
            {"who": "you", "text": "Tell me all of it.", "at": at(50)},
            {"who": "talker", "text": "The login page moved to the new router. Want the next part?", "at": at(45)},
            {"who": "you", "text": "Yes.", "at": at(40)},
            {"who": "talker", "text": "The session cookie now expires after a week. Go on?", "at": at(35)}]})
    async with ClaudeSDKClient(options=make_options()) as client:
        said, calls, sent = await turn(client, "Yes, go on.")
    ok(("logout" in said.lower() or "tab" in said.lower()) and "router" not in said.lower(),
       "after a restart mid-answer, voice goes on with the next part", said)

    # A reload: a few words that it's back and listening; no greeting, nothing sent.
    restart("voice was on before this load")
    FEED.pending.append(restart_note())
    talker.GREETING["on"] = True  # as Talker._greet sets it
    async with ClaudeSDKClient(options=make_options()) as client:
        said, calls, sent = await turn(client, "")
    talker.GREETING["on"] = False
    ok(bool(said) and len(said.split()) <= 12 and not sent and not re.search(r"\b(hi|hey|hello|welcome)\b|what would you like|reload", said.lower()),
       f"after a reload, a few words that voice is back, no greeting (calls {calls})", said)

    # A language switch: a few words, in the new language, that it's back; no greeting, nothing sent.
    talker.LANGUAGE["code"] = "es"
    restart("the language is now es", "Spanish")
    FEED.pending.append(restart_note())
    FEED.add({"type": "history", "messages": [], "voice": [{"who": "you", "text": "Switch to Spanish.", "at": at(10)}]})
    talker.GREETING["on"] = True  # as Talker._greet sets it
    async with ClaudeSDKClient(options=make_options()) as client:
        said, calls, sent = await turn(client, "")
    talker.GREETING["on"] = False
    talker.LANGUAGE["code"] = "en"
    ok(bool(said) and len(said.split()) <= 15 and not sent and bool(re.search(r"[áéíóñ¿¡]|\b(estoy|aquí|ya|de nuevo|español|vuelta|listo)\b", said.lower())),
       "after a language switch, a few words in the new language that voice is back", said)
    ok(not re.search(r"(qué|en qué) (quieres|te gustaría|puedo)|what would you like", said.lower()), "and no 'what would you like to work on'", said)


offline()
if "--offline" not in sys.argv:
    asyncio.run(live())
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
