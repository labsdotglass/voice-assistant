"""Imported first by every check: puts the repo root on the import path, so a check finds the modules it tests
however it's run (uv run run_tests.py, or on its own: uv run tests/test_naming.py). It also points voice's own
files a check could write (the talker's record of its checks, your settings, your instructions about how it
talks) at scratch copies, as run_tests.py does, so a check run on its own never touches yours."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_scratch = Path(tempfile.mkdtemp())
for _var, _name in (("VOICE_CHECKS_FILE", "checks.json"), ("VOICE_SETTINGS_FILE", "settings.json"),
                    ("VOICE_INSTRUCTIONS_FILE", "instructions.txt")):
    os.environ.setdefault(_var, str(_scratch / _name))
