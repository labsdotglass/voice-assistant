"""Tech terms in a Hebrew reply show in English on screen; the voice still gets the Hebrew (no models).

hebrew_terms.for_screen is what the talker's transcript goes through in Hebrew. Checks real replies: terms and
their plurals become English, a prefix stays joined with a hyphen, and words that only start like a term stay
Hebrew. (talker.py sends the transcript through it only when voice runs in Hebrew.)
"""

import _root  # noqa: F401  (the repo root on the import path)
import sys

from hebrew_terms import for_screen

CASES = [  # (the talker's Hebrew, what the screen shows)
    ("פתחתי פול ריקוסט בגיטהאב, והקומיט האחרון נדחף לענף.", "פתחתי pull request ב-GitHub, וה-commit האחרון נדחף לענף."),
    ("הדיפלוי נכשל כי הטוקן של ה-API פג.", "ה-deploy נכשל כי ה-token של ה-API פג."),
    ("יש לי שני באגים ושלושה טסטים שנכשלו.", "יש לי שני bugs ושלושה tests שנכשלו."),
    ("בקיצור, פי.אר.", "בקיצור, PR."),
    ("שלחתי לקלוד את הלוגים.", "שלחתי ל-Claude את ה-logs."),
    ("הסשן סיים את העבודה.", "הסשן סיים את העבודה."),
    ("גיטרה זה לא גיט.", "גיטרה זה לא Git."),
    ("פושטקים ובאגטים", "פושטקים ובאגטים"),
]
results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def main():
    for hebrew, screen in CASES:
        got = for_screen(hebrew)
        ok(got == screen, f"{hebrew} -> {got}" + ("" if got == screen else f"  (expected {screen})"))
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


main()
