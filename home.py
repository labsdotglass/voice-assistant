"""Where voice keeps what's yours: settings, voice prints and recordings, greetings and switches (~/.voice-assistant).

The folder is private to you (0700): it holds recordings of your voice and bits of past conversations. Standard
library only: the first run's window (onboarding.py) imports it before anything else is set up.
"""

from pathlib import Path

HOME = Path.home() / ".voice-assistant"
# Voice's runtime files (its socket, who had voice last, restart flags, other sessions' notes) and its logs: in
# here, private to you, not in the shared /tmp where another user on the Mac could read or claim them.
# The mod (hooks/register.tsx, runPaths) uses the same names.
RUN = HOME / "run"
LOGS = HOME / "logs"
SOCKET = RUN / "voice.sock"
LOG = LOGS / "voice.log"


def private(folder: Path) -> Path:
    """The folder, made if missing and readable only by you (0700)."""
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    return folder


private(HOME)
