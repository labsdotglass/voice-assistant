"""Imported first by every check: puts the repo root on the import path, so a check finds the modules it tests
however it's run (uv run run_tests.py, or on its own: uv run tests/test_naming.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
