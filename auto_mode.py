"""Auto mode for new Claude Code sessions: whether `permissions.defaultMode` in ~/.claude/settings.json is
"auto", and turning it on when the person chooses to (setup.py --auto-mode).

Voice can't approve a permission prompt, so a session voice drives stops at every one unless it runs in auto
mode. The app ignores the mode a session's first turn ran in (`claude -p --permission-mode`), so this global
default is the only lever. Nothing here changes it unless asked. Standard library only: onboarding.py uses it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def settings_file() -> Path:
    return Path.home() / ".claude" / "settings.json"  # read at each call, so a test's HOME counts


def mode(file: Path | None = None) -> str:
    """The default mode new sessions start in: "auto", another mode's name, or "default" when none is set."""
    try:
        return str(json.loads((file or settings_file()).read_text()).get("permissions", {}).get("defaultMode") or "default")
    except (OSError, ValueError, AttributeError):
        return "default"


def turn_on(file: Path | None = None) -> str:
    """Sets `permissions.defaultMode` to "auto", keeping everything else in the file and a copy of it as it was
    (settings.json.before-auto-mode): -> what happened, in a line."""
    file = file or settings_file()
    if mode(file) == "auto":
        return "auto mode was already on"
    try:
        current = json.loads(file.read_text()) if file.exists() else {}
    except ValueError:
        return f"not changed: {file} isn't valid JSON, so it's left as it is"
    if not isinstance(current, dict):
        return f"not changed: {file} isn't a settings object"
    if file.exists():
        shutil.copyfile(file, file.with_name(file.name + ".before-auto-mode"))
    permissions = current.get("permissions") if isinstance(current.get("permissions"), dict) else {}
    current["permissions"] = {**permissions, "defaultMode": "auto"}
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(current, indent=2) + "\n")
    return f"auto mode is on: new sessions start in it ({file})"
