"""Speech voice heard and dropped, and whether to tell you: a ring flashing on the orb and a short sound.

Three places drop what voice heard: the voice check (not your voice, SpeakerGate), Whisper rating it as
likely silence (WhisperKeepingYou) and the hallucination filter. Each reports here. Every drop takes the orb
out of thinking at once, since no reply is coming. Only some get the cue: most drops are other voices (the
TV, people in the room, voice's own echo), and a cue on each would keep firing while they talk.

The cue is for speech that was likely yours:
- the voice check let it through as you (too short to judge, or no voice print) and Whisper or the filter
  dropped it, except for a filler sound ("um"), which says nothing;
- or the check scored it just under the cutoff (NEAR and up), where your own speech gets dropped: in the
  logs, those are mostly you ("Yes.", "Yeah, that's good."), and lower scores are mostly the TV and echo.

At most one cue per COOLDOWN seconds: a burst of near misses is one "I didn't catch that", not five.
"""

import time

from loguru import logger

NEAR = 0.25  # voice check scores from here to the cutoff: likely you, dropped anyway
COOLDOWN = 8.0
FILLERS = {"um", "uh", "hmm", "so", "you"}  # Whisper's sounds from noise, or yours that need no answer


def words_of(text: str) -> str:
    return " ".join("".join(c for c in text.lower() if c.isalpha() or c == " ").split())


class Drops:
    def __init__(self):
        self.on_drop = None  # any drop: e.g. the orb leaves thinking
        self.on_cue = None  # a drop of what was likely you: the orb's ring and sound
        self._last_cue = float("-inf")

    def report(self, reason: str, text: str, is_you: bool, score: float | None = None) -> bool:
        """A drop: reason is "not you", "silence" or "hallucination"; is_you and score are the voice
        check's verdict (score None when it didn't judge). Returns whether it gave the cue."""
        if self.on_drop:
            self.on_drop()
        likely_you = (is_you and words_of(text) not in FILLERS) or (score is not None and score >= NEAR)
        now = time.monotonic()
        cue = likely_you and now - self._last_cue >= COOLDOWN
        logger.info(f"[dropped] {reason}: {text.strip()!r}" + (f" (score {score:.2f})" if score is not None else "")
                    + (": cue" if cue else ", in the cooldown: no cue" if likely_you else ": no cue, likely not you"))
        if cue:
            self._last_cue = now
            if self.on_cue:
                self.on_cue()
        return cue


DROPS = Drops()
