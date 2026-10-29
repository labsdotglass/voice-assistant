"""Hebrew voice reads marked words as marked, words Phonikud misreads as they should sound, and English words
the way Israelis say them (no models).

Phonikud guesses every Hebrew word's vowels and overwrites niqqud it's given; voice keeps the talker's niqqud
(per letter, so a prefix keeps Phonikud's vowel), its stress mark alone too, and reads KNOWN_WORDS as listed,
with any prefix. Checked on Phonikud's IPA, the sounds Kokoro is given.
"""

import _root  # noqa: F401  (the repo root on the import path)
import sys

from loguru import logger

from hebrew_voice import load_hebrew

STRESS = "֫"
CASES = [  # (text, sound that must be in its IPA, what's checked)
    ("הוא אוהב את הסַפָּר", "hasapˈaʁ", "the talker's niqqud is kept: the barber, not the book"),
    ("הוא אוהב את הספר", "hasˈefeʁ", "without niqqud, Phonikud's reading"),
    ("הלכתי לסַפָּר אתמול", "lesapˈaʁ", "a marked stem keeps Phonikud's prefix vowel"),
    (f"הס{STRESS}שן סיים", "hasˈeʃen", "a stress mark alone is kept"),
    ("שָׁלַחְתְּ את הקובץ", "ʃalˈaχt", "a feminine 'you' verb, marked, is read feminine"),
    ("בסדר, אני מעביר את זה לסשן.", "lesˈeʃen", "a known word with a prefix (Phonikud alone: lesˈeʃan)"),
    ("הסשן סיים ופנוי עכשיו.", "hasˈeʃen", "a known word, sentence-first (Phonikud alone: hasʃˈen)"),
    ("יש שני סשנים פתוחים.", "seʃenˈim", "a known word's plural (Phonikud alone: sˈʃanim)"),
    ("עברתי מהסשן הקודם.", "mehasˈeʃen", "a known word with two prefixes"),
    ("הבנתי את הבעיה בכיוון הטקסט.", "hatˈekst", "words not on the list are untouched"),
    ("הסשן מעדכן עכשיו את ה-PR עם השינוי.", "hapiˈaʁ", "an English acronym is said, with its Hebrew prefix (was dropped)"),
    ("הסשן שומר את השינוי ב-commit.", "bekemˈit", "an English word, Israeli accent (was 'ommit')"),
    ("זה Pull Request, בקיצור PR.", "pˈul ʁekwˈest", "English words in a row (were 'ull euest')"),
    ("הבדיקות עברו, ועשיתי push ל-GitHub.", "leɡithˈab", "an English name with a prefix (was 'litub')"),
    ("פתחתי פול ריקווסט", "pˈul ʁikvˈest", "listed words (Phonikud alone: 'pol', then 'ʁikvˈust')"),
    ("הטוקן של ה-API פג", "hatˈoken", "a listed word (Phonikud alone: 'tokˈan')"),
    ("שהטוקן פג", "ʃehatˈoken", "a listed word after two prefixes, no doubled vowel"),
    ("והטסטים עוברים", "vehatestˈim", "a listed plural (Phonikud alone: 'tastˈim')"),
    ("נדחף לבראנץ'.", "lebʁˈantʃ", "a listed word ending in a geresh keeps its prefix vowel (was 'lbʁˈantʃ')"),
]


def main():
    logger.remove()
    hebrew = load_hebrew()
    results = []
    for text, sound, what in CASES:
        ipa = hebrew.ipa(text)
        good = sound in ipa  # the sounds and where the stress falls
        results.append(good)
        print(f"{'PASS' if good else 'FAIL'}  {what}: {text} -> {ipa}")
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


main()
