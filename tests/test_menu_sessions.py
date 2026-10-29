"""The orb's menu by voice, part 3: starting a new session, going to one by name, and work for the
session still going to the session (asks Haiku)."""

import _root  # noqa: F401  (the repo root on the import path)
from voice_menu_check import run, sessions, work_for_session

run(sessions, work_for_session)
