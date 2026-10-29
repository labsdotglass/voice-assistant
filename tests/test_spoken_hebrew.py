"""In Hebrew, the talker speaks spoken Israeli Hebrew, not written Hebrew (asks Haiku; run by hand).

A measure, not a gate: Haiku passes about half the time (Sonnet 8/9, at twice the time to its first words), so
it's left out of run_tests.py's default run. Use it to compare a change to the Hebrew rule against main.

The talker relays session answers written formally in English. Its Hebrew must carry none of the markers of
written or official register below. They live here only, never in the talker's prompt (Haiku copies examples).
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
from talker import FEED, make_options

# Words and constructions people write but don't say (formal "now", "which", "I am", "is", "in order to", ...).
WRITTEN = ["כעת", "אשר", "הנני", "הינו", "הינה", "הנה ה", "על מנת", "ברצוני", "באפשרותך", "ביכולתי", "אנא", "לפיכך",
           "עליך", "הנך", "יש ביכולת", "במידה ו", "בהתאם ל", "כמו כן", "יתר על כן", "זאת ועוד", "בטרם", "לכשת", "נבע", "הוחל", "אין צורך", "פריסה", "הפריסה", "הפצה", "ההפצה"]
ANSWERS = [
    "The deployment has been completed successfully. All services are operational, and no further action is required at this time.",
    "Please be advised that the requested modifications to the configuration file have been applied, in order to resolve the reported issue.",
    "I have examined the logs thoroughly. The failure was caused by an expired authentication token, which has now been renewed.",
]
results = []


async def spoken(answer: str) -> str:
    FEED.pending, FEED.queued, FEED.busy = [], [], False
    FEED.add({"type": "turn_complete", "durationMs": 20000, "isAborted": False, "answer": answer})
    said = []
    async with ClaudeSDKClient(options=make_options()) as client:
        with redirect_stdout(io.StringIO()):
            await client.query(talker.turn(FEED.drain(), ""))
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    said += [b.text for b in msg.content if isinstance(b, TextBlock)]
    return " ".join(said).strip()


async def main():
    talker.LANGUAGE["code"] = "he"
    for answer in ANSWERS:
        said = await spoken(answer)
        found = [w for w in WRITTEN if re.search(rf"(?<![א-ת]){w}", said)]
        letters = re.findall(r"[A-Za-zא-ת]", said)
        hebrew = bool(letters) and sum("א" <= c <= "ת" for c in letters) / len(letters) > 0.9
        good = hebrew and not found
        results.append(good)
        print(f"{'PASS' if good else 'FAIL'}  {said}" + (f"\n        written-register words: {found}" if found else "")
              + ("" if hebrew else "\n        not in Hebrew"))
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
