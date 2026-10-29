"""The orb's menu by voice, part 1: the transcript, the voice, and how your words are passed on
(asks Haiku). The rest of the menu is in test_menu_*.py; the shared code is in voice_menu_check.py."""

import _root  # noqa: F401  (the repo root on the import path)
from voice_menu_check import CASES, menu_actions, run

run(lambda: menu_actions(CASES[:6]))
