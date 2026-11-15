"""Mute: voice stops hearing and speaking until you unmute, without ending voice or losing the conversation.

For phone calls. While muted, the mic's audio is replaced by silence before anything listens to it (the VAD,
Whisper, the voice check), so nothing said on the call is transcribed or answered; nothing is spoken, and what
was being said stops at once. News from sessions waits in the feed and is told once you unmute. The orb
shows it (gray, with a crossed-out mic in its middle); you unmute from its menu or by clicking it, since
voice can't hear you ask. It isn't kept across a restart: voice comes back unmuted.
"""

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
