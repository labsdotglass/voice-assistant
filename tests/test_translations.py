"""Translations (no model): every per-language table covers every language in settings.LANGUAGES.

A per-language table is any module-level dict in the repo's own modules keyed by language codes, found
by reading the code (so a new one is checked without being listed here), plus the ones named in
KNOWN, which must keep being found. Each must have an entry for every language, none of them empty,
and a text one mustn't just repeat the English. Fails naming the table and the languages it misses.

The first run's window has its words in orb-page/src/first-run-text.json: every key has every
language, translated, in 25 words or fewer, and every key the page asks for exists.
"""

import _root  # noqa: F401  (the repo root on the import path)
import ast
import json
import re
import sys
from pathlib import Path

import settings

ROOT = Path(__file__).resolve().parent.parent
CODES = [code for code, *_ in settings.LANGUAGES]
FIRST_RUN_TEXT = ROOT / "orb-page" / "src" / "first-run-text.json"
FIRST_RUN_PAGE = ROOT / "orb-page" / "src" / "tour.tsx"
MAX_WORDS = 25
KNOWN = {"settings.py": {"VOICES_BY_LANGUAGE", "VOICE_SAMPLE"}, "speaker.py": {"PHRASES", "EXTRA_PHRASES"}}
results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def tables(source: str) -> dict[str, ast.Dict]:
    """The module-level dicts keyed by language codes: two or more of their keys are codes, English among them."""
    found = {}
    for node in ast.parse(source).body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else getattr(node, "target", None)
        value = getattr(node, "value", None)
        if isinstance(target, ast.Name) and isinstance(value, ast.Dict):
            keys = [k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if "en" in keys and len(set(keys) & set(CODES)) >= 2:
                found[target.id] = value
    return found


def gaps(name: str, table: ast.Dict) -> list[str]:
    entries = {k.value: v for k, v in zip(table.keys, table.values) if isinstance(k, ast.Constant)}
    problems = [f"{name} has no {code}" for code in CODES if code not in entries]
    english = entries.get("en")
    for code, v in entries.items():
        if isinstance(v, (ast.Constant, ast.List, ast.Tuple, ast.Dict)) and not ast.literal_eval(v):
            problems.append(f"{name}[{code!r}] is empty")
        elif code != "en" and english is not None and ast.dump(v) == ast.dump(english):
            problems.append(f"{name}[{code!r}] is the English one, untranslated")
    return problems


def text_gaps(texts: dict) -> list[str]:
    """What's wrong with the first run's texts: a language missing, empty, left in English, or over MAX_WORDS words."""
    problems = []
    for key, by_code in texts.items():
        for code in CODES:
            value = by_code.get(code)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{key} has no {code}")
            elif code != "en" and value == by_code.get("en"):
                problems.append(f"{key}[{code!r}] is the English one, untranslated")
            elif len(value.split()) > MAX_WORDS:
                problems.append(f"{key}[{code!r}] has {len(value.split())} words")
    return problems


def first_run_texts():
    ok(FIRST_RUN_TEXT.exists(), f"{FIRST_RUN_TEXT.relative_to(ROOT)} exists")
    if not FIRST_RUN_TEXT.exists():
        return
    texts = json.loads(FIRST_RUN_TEXT.read_text())
    problems = text_gaps(texts)
    ok(bool(texts) and not problems, f"first-run texts: {len(texts)} keys, each in all {len(CODES)} languages, {MAX_WORDS} words or fewer"
       + (": " + "; ".join(problems) if problems else ""))
    # Every t("key") the page asks for exists, so no screen shows a bare key.
    asked = set(re.findall(r"""\bt\(['"]([\w.-]+)['"]""", FIRST_RUN_PAGE.read_text()))
    missing = sorted(asked - texts.keys())
    ok(bool(asked) and not missing, f"every text tour.tsx asks for is in first-run-text.json" + (": missing " + ", ".join(missing) if missing else ""))
    # The check catches a missing language, an English copy and a long text.
    broken = text_gaps({"a": {"en": "Hi", "es": "Hi"}, "b": {**{c: "x" for c in CODES}, "fr": "w " * 26}})
    ok(any("a has no he" in p for p in broken) and any("'es'] is the English" in p for p in broken)
       and any("'fr'] has 26 words" in p for p in broken), "a first-run text missing a language, untranslated or too long fails")


def main():
    sources = {p.name: p.read_text() for p in sorted(ROOT.glob("*.py")) if not p.name.startswith("test_")}
    found = {(f, name): t for f, src in sources.items() for name, t in tables(src).items()}
    for f, names in KNOWN.items():
        for name in names:
            ok((f, name) in found, f"{f}: {name} is found as a per-language table")
    for (f, name), table in sorted(found.items()):
        problems = gaps(name, table)
        ok(not problems, f"{f}: {name} covers all {len(CODES)} languages" + (": " + "; ".join(problems) if problems else ""))

    # The check catches a missing language, an empty entry and a copied English one.
    broken = ast.parse('T = {"en": "Hello", "he": "", "es": "Hello", "fr": "Bonjour"}').body[0].value
    problems = gaps("T", broken)
    ok(any("no it" in p for p in problems), "a table missing a language fails")
    ok(any("'he'] is empty" in p for p in problems), "an empty entry fails")
    ok(any("'es'] is the English" in p for p in problems), "an entry left in English fails")
    ok("T" in tables('T = {"en": 1, "fr": 2}') and not tables('T = {"en": 1, "x": 2}'), "a new table is picked up by its keys")

    first_run_texts()

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


main()
