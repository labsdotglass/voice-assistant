"""In Hebrew, the talker marks gendered "you" words once it knows the user's gender, and nothing else (asks Haiku).

The voice guesses each Hebrew word's vowels from its letters, so a "you" word spelled the same for a man and a
woman ("שלחת": shalachta / shalacht) is read masculine unless marked. After the user says she's a woman, each
such word the talker uses is marked and read feminine (rephrasing without one is fine). Replies with nothing
gendered have no niqqud at all.
"""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import re
import sys
from contextlib import redirect_stdout

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, TextBlock

import talker
from hebrew_voice import NIKUD, load_hebrew
from talker import FEED, make_options

SHE = "רק שתדע, אני אישה."  # the user tells the talker her gender, as a fact (not a request about how to talk)
PLAIN = ["The tests passed and the change is pushed.", "The session is done and is waiting for your next request."]
TO_HER = ["You sent the right file, and you fixed the last error yourself.", "You asked for the logs earlier; you can open them now."]
# "You" past verbs spelled the same for both genders: read feminine they end in a bare "t", masculine in "ta".
YOU_PAST = {"שלחת", "תיקנת", "ביקשת", "עשית", "פתחת", "כתבת", "אמרת", "רצית", "שאלת", "סיימת"}
results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


async def spoken(answer: str, first: str = "") -> str:
    """The talker's words for the session's answer, after the user said `first` (if anything)."""
    FEED.pending, FEED.queued, FEED.busy = [], [], False
    said = []
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(io.StringIO()):
            if first:
                await client.query(talker.turn("", first))
                async for _ in client.receive_response():
                    pass
            FEED.add({"type": "turn_complete", "durationMs": 20000, "isAborted": False, "answer": answer})
            await client.query(talker.turn(FEED.drain(), ""))
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    said += [b.text for b in msg.content if isinstance(b, TextBlock)]
    return " ".join(said).strip()


async def main():
    talker.LANGUAGE["code"] = "he"
    hebrew = load_hebrew()
    for answer in PLAIN:
        said = await spoken(answer)
        ok(not NIKUD.search(said), f"nothing gendered, no niqqud: {said}")
    for answer in TO_HER:
        said = await spoken(answer, first=SHE)
        marked = [w for w in said.split() if NIKUD.search(w)]
        ipa = hebrew.ipa(said)
        plain = lambda w: NIKUD.sub("", w).strip(".,?!").lstrip("ו")
        heard = [s for w, s in zip(said.split(), ipa.split()) if plain(w) in YOU_PAST]  # Phonikud keeps the words in order
        masculine = [s for s in heard if s.rstrip(".,?!").endswith("ta")]
        ok(not masculine and len(marked) <= 4,  # judged by how it sounds: context ("את") can settle it unmarked
           f"to her, read feminine: {said}\n        marked: {marked}  ->  {ipa}")
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
