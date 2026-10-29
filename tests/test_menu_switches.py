"""The orb's menu by voice, part 2: learn my voice, the tour, progress updates, the next session,
and Stop voice ending voice (asks Haiku)."""

import _root  # noqa: F401  (the repo root on the import path)
from voice_menu_check import CASES, menu_actions, run, stop_voice

run(lambda: menu_actions(CASES[6:]), stop_voice)
