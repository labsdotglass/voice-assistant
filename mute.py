"""Mute: voice stops hearing and speaking until you unmute, without ending voice or losing the conversation.

For phone calls. While muted, voice still listens, but only to unmute: anything else you say (your side of the
call) is never answered or sent, and saying "unmute" alone, or a short phrase with it (is_unmute), brings voice
back. Nothing is spoken, and what was being said stops at once. News from sessions waits in the feed and is told
once you unmute. The orb shows it (gray, with a crossed-out mic in its middle); its menu, or a click on it,
unmutes too. It isn't kept across a restart: voice comes back unmuted.
"""

import unicodedata

import time

from loguru import logger


class Mute:
    def __init__(self):
        self.on = False
        self.since = 0.0  # when it was muted (monotonic)
        self.last_minutes = 0  # how long the last mute lasted, in whole minutes (for what's said after it)
        self.on_change = None  # (muted) -> None: the talker stops or catches up, the orb shows it

    def set(self, muted: bool, why: str = "") -> bool:
        """Mute or unmute; False if it already was."""
        if muted == self.on:
            return False
        self.on = muted
        if muted:
            self.since = time.monotonic()
            logger.info(f"[mute] muted{f' ({why})' if why else ''}: voice neither hears nor speaks")
        else:
            self.last_minutes = int((time.monotonic() - self.since) // 60)
            logger.info(f"[mute] unmuted after {time.monotonic() - self.since:.0f}s{f' ({why})' if why else ''}")
        if self.on_change:
            self.on_change(muted)
        return True


MUTE = Mute()

# What unmutes, said while muted: the English word in any language (Whisper often keeps it), plus each language's own.
UNMUTE_EN = ("unmute", "un-mute", "un mute")
UNMUTE_WORDS = {
    "en": ("turn the sound back on", "you can talk again"),
    "he": ("בטל השתקה", "ביטול השתקה", "תבטל השתקה", "בטלי השתקה", "אנמיוט"),
    "es": ("activar sonido", "quitar silencio", "desactivar silencio", "desmutear"),
    "fr": ("réactiver le son", "rétablir le son", "réactive le son", "démute"),
    "it": ("riattiva l'audio", "togli il muto", "riattiva il suono", "smuta"),
    "pt": ("ativar som", "tirar do mudo", "desmutar", "ativa o som"),
    "hi": ("अनम्यूट", "म्यूट हटाओ", "आवाज़ चालू करो"),
}
UNMUTE_MOST_WORDS = 6  # longer is talk on the call that happens to say it ("can you unmute yourself? I can't hear you")


def is_unmute(text: str, language: str) -> bool:
    """Whether what was heard while muted asks voice to unmute: a short utterance with an unmute word in it."""
    # Punctuation out; letters, their marks (Hindi's vowel signs, Hebrew's points) and digits stay.
    said = " ".join("".join(c if unicodedata.category(c)[0] in "LMN" or c in "'-" else " " for c in text.lower()).split())
    if not said or len(said.split()) > UNMUTE_MOST_WORDS:
        return False
    return any(w in said for w in UNMUTE_EN + UNMUTE_WORDS.get(language, ()))
