"""Your voice settings, chosen from the orb's right-click menu and kept in ~/.voice-assistant/settings.json.

language: the one language you talk in, and it answers in. voice: the voice it speaks with, one of that
language's. pass_on: how it passes your words to the session.
"""

import json
import re
import os
from pathlib import Path

from loguru import logger

from home import HOME

# VOICE_SETTINGS_FILE: another file (each check gets an empty one, so your own settings never change its outcome).
FILE = Path(os.environ.get("VOICE_SETTINGS_FILE") or HOME / "settings.json")

# (id, menu label): Kokoro's best-rated American voices, four female and four male (its grades, best
# first; the male voices grade lower than the female ones). The male voices are picked to sound unlike
# each other: Puck was out, nearly the same as Fenrir by the speaker-ID model (0.73); Eric is at most
# 0.25 to any of the others. Kore replaced Nicole: at most 0.27 to the other female voices. Heart and
# Bella are the most alike pair left (0.66).
VOICES = [
    ("af_heart", "Heart (female)"),
    ("af_bella", "Bella (female)"),
    ("af_sarah", "Sarah (female)"),
    ("af_kore", "Kore (female)"),
    ("am_michael", "Michael (male)"),
    ("am_fenrir", "Fenrir (male)"),
    ("am_onyx", "Onyx (male)"),
    ("am_eric", "Eric (male)"),
]

# (id, menu label, Whisper's language code, Kokoro's language code, the final Whisper model or None for
# the default one), read from languages.json, which also holds each language's models and download size.
# Hebrew is heard by ivrit.ai's Hebrew Whisper (half stock Whisper's errors) and spoken
# by Hebrew Kokoro (hebrew_voice.py); the others by stock Whisper and Kokoro's own voices for them.
# Japanese and Chinese are left out: Kokoro reads them through espeak, which says them badly.
_LANGUAGE_FILE = json.loads((Path(__file__).parent / "languages.json").read_text())["languages"]
LANGUAGES = [(l["code"], l["label"], l["whisper"], l["kokoro"], l["model"]) for l in _LANGUAGE_FILE]


def download_gb(code: str) -> float:
    """What a language's first start downloads, in GB (languages.json: its models plus the shared ones)."""
    return next(l["download_gb"] for l in _LANGUAGE_FILE if l["code"] == code)

# Each language's voices, the first one its default.
VOICES_BY_LANGUAGE = {
    "en": VOICES,
    "he": [("he_shaul", "Shaul (male)")],
    "es": [("ef_dora", "Dora (female)"), ("em_alex", "Alex (male)"), ("em_santa", "Santa (male)")],
    "fr": [("ff_siwis", "Siwis (female)")],
    "it": [("if_sara", "Sara (female)"), ("im_nicola", "Nicola (male)")],
    "pt": [("pf_dora", "Dora (female)"), ("pm_alex", "Alex (male)"), ("pm_santa", "Santa (male)")],
    "hi": [("hf_alpha", "Alpha (female)"), ("hf_beta", "Beta (female)"), ("hm_omega", "Omega (male)"), ("hm_psi", "Psi (male)")],
}

# What the voice says when you pick it, in its language.
VOICE_SAMPLE = {
    "en": "This is how I sound now.",
    "he": "ככה אני נשמע עכשיו.",
    "es": "Así es como sueno ahora.",
    "fr": "Voici ma voix maintenant.",
    "it": "Ecco come suono adesso.",
    "pt": "É assim que eu soo agora.",
    "hi": "अब मेरी आवाज़ ऐसी है।",
}

# (id, menu label, how the talker writes the prompt it sends)
PASS_ON = [
    ("concise", "Concise",
     "Write the prompt short and to the point: the user's request in as few words as carry it, keeping their intent."),
    ("cleaned", "Cleaned up",
     "Write the prompt in the user's own wording, cleaned up: leave out filler words and verbal tics wherever they "
     "fall, even at the very end, plus false starts, repetitions and obvious transcription slips, and fix the grammar only where it's broken. Keep their words, their order and "
     "every detail they gave; don't shorten, summarize or rephrase it."),
]

DEFAULTS = {"language": "en", "voice": os.environ.get("VOICE", "af_heart"), "pass_on": "concise"}
VALID = {"language": {code for code, *_ in LANGUAGES},
         "voice": {v for vs in VOICES_BY_LANGUAGE.values() for v, _ in vs} | {DEFAULTS["voice"]},
         "pass_on": {m for m, _, _ in PASS_ON}}


def load() -> dict:
    try:
        saved = json.loads(FILE.read_text())
    except (OSError, ValueError):
        saved = {}
    current = {k: saved[k] if saved.get(k) in VALID[k] else d for k, d in DEFAULTS.items()}
    if current["language"] != "en" and current["voice"] not in {v for v, _ in voices(current["language"])}:
        current["voice"] = voices(current["language"])[0][0]  # a voice of another language: this one's default
    return current


def voices(language: str) -> list:
    return VOICES_BY_LANGUAGE.get(language, VOICES)


def language(code: str) -> tuple:
    """(id, label, whisper code, kokoro code, final Whisper model or None) for a language id."""
    return next((lang for lang in LANGUAGES if lang[0] == code), LANGUAGES[0])


def voice_language(voice: str) -> str:
    """The language a voice belongs to."""
    return next((code for code, vs in VOICES_BY_LANGUAGE.items() if voice in {v for v, _ in vs}), "en")


def language_rule(code: str) -> str:
    """How the talker speaks, for a language other than English ("" for English)."""
    if code == "en":
        return ""
    label = language(code)[1]
    rule = (f"\n\nLanguage: the user talks to you in {label}, and you always speak to them in {label}, whatever language "
            f"the session writes in: put what it wrote into natural spoken {label}. What you send to the session stays "
            f"in the user's own words and language. Write numbers, versions, times and dates out as spoken words.")
    if code == "he":
        rule += (" Speak spoken Israeli Hebrew, the way people talk to each other out loud, never written Hebrew: "
                 "everyday words and spoken grammar, short direct sentences, nothing formal, literary, official or "
                 "textbook-like, even when the session wrote formally or in English; say its content the way a "
                 "colleague would say it across the desk, with the words Israelis in tech actually say, borrowed ones "
                 "included, rather than the official Hebrew coined for them. Write plain Hebrew, without niqqud, with one exception. The voice "
                 "that reads your words aloud guesses each word's vowels from its letters alone. So once you know the "
                 "user's gender, every word you address to them whose spelling is the same for a man and a woman but "
                 "sounds different gets full niqqud, so it's read in their gender. Everything else stays plain. "
                 "Write English technical terms, "
                 "commands and names the way Israelis say them, in Hebrew letters, never in Latin letters. You don't "
                 "know the user's grammatical gender until they tell you: until then, word what you say to them so "
                 "it doesn't depend on it.")
    return rule


def save(key: str, value: str) -> bool:
    """Keep one setting. -> False if it isn't a valid choice."""
    if value not in VALID.get(key, ()):
        logger.warning(f"[settings] ignored {key}={value!r}: not a choice")
        return False
    current = load()
    current[key] = value
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(current, indent=2))
    logger.info(f"[settings] {key} = {value}")
    return True


def pass_on_rule(mode: str) -> str:
    return next(rule for m, _, rule in PASS_ON if m == mode)


EXPERIMENTAL = {"he"}  # languages the menu marks "(experimental)", below a divider at the bottom of its list
MENU_DIVIDER = ["-", ""]  # a divider in one of the menu's lists (the helper draws it as a separator)


def menu(current: dict) -> dict:
    """What the orb's menu shows: the choices and which ones are ticked."""
    stable = [[code, label] for code, label, *_ in LANGUAGES if code not in EXPERIMENTAL]
    trying = [[code, f"{label} (experimental)"] for code, label, *_ in LANGUAGES if code in EXPERIMENTAL]
    return {**current, "voices": voices(current.get("language", "en")),
            "languages": stable + ([MENU_DIVIDER] + trying if trying else []),
            "modes": [[m, label] for m, label, _ in PASS_ON]}


def find_language(name: str) -> str | None:
    """A language by what you'd say ("Hebrew", "he", "עברית", or the menu's "Hebrew (experimental)"); None if it isn't one."""
    name = re.sub(r"\s*\(?experimental\)?\s*$", "", name.strip().lower())
    for code, label, *_ in LANGUAGES:
        if name in (code, label.lower()) or (code == "he" and name == "עברית"):
            return code
    return None


def find_voice(name: str, language: str = "en") -> str | None:
    """A voice from the menu by what you'd say: its name ("Michael") or id; None if it isn't one."""
    name = name.strip().lower()
    for vid, label in voices(language):
        if name in (vid, label.split()[0].lower()):
            return vid
    return None


def find_mode(name: str) -> str | None:
    """A Pass on my words mode by what you'd say ("concise", "cleaned up"); None if it isn't one."""
    name = name.strip().lower().replace("-", " ")
    for mode, label, _ in PASS_ON:
        if name in (mode, label.lower()) or label.lower().startswith(name):
            return mode
    return None
