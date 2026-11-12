"""The programs voice builds on this Mac: the audio helper, Voice Assistant.app (its launcher) and the first run's window.

They live in ~/.voice-assistant/bin, not in the plugin's folder. Each update installs into a new folder of its own
(~/.claude/plugins/cache/voice-assistant/voice-mode/<version>/), so programs kept there were built again on every
update's first start. Here an update finds them already built.

A program is rebuilt only when what it's built from changed: the content of its source (and, for the app, its icon
and Info.plist), recorded in bin/built.json. Not the files' dates: an update's files are all new. macOS ties the
microphone permission to the app's exact build, so rebuilding it only when its source really changed keeps
that permission. A build goes to a temporary name and then replaces the old one, so another start never runs a
half-built program. Standard library only: early_orb builds before the daemon's heavy imports, and the first
run's window (onboarding.py) before anything is set up.
"""

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import home

HERE = Path(__file__).parent
BIN = home.HOME / "bin"
BUILT = BIN / "built.json"  # what each program was built from: {name: fingerprint}

HELPER = BIN / "audio_helper"
HELPER_SOURCE = HERE / "audio_helper" / "main.swift"
# The helper runs through Voice Assistant.app, so macOS lists its microphone permission under that name.
APP = BIN / "Voice Assistant.app"
LAUNCHER = APP / "Contents" / "MacOS" / "voice-assistant"
LAUNCHER_SOURCE = HERE / "audio_helper" / "launcher.swift"
APP_ICON = HERE / "audio_helper" / "VoiceAssistant.icns"  # drawn by audio_helper/make_icon.swift
APP_INFO = {
    "CFBundleName": "Voice Assistant",
    "CFBundleDisplayName": "Voice Assistant",
    # A new id is a new app to macOS: it asks once more for the microphone (and Accessibility). The Info.plist
    # is part of the app's fingerprint, so a change here rebuilds the app on the next start.
    "CFBundleIdentifier": "glass.labs.voice-assistant",
    "CFBundleExecutable": "voice-assistant",
    "CFBundleIconFile": "VoiceAssistant",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "1.0",
    "CFBundleVersion": "1",
    "LSUIElement": True,
    "NSMicrophoneUsageDescription": "Voice Assistant listens so you can talk to Claude Code.",
}
ONBOARDING = BIN / "onboarding"
ONBOARDING_SOURCE = HERE / "audio_helper" / "onboarding.swift"


def fingerprint(*parts: Path | bytes) -> str:
    """A hash of what a program is built from: files' contents, and any bytes given as they are."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else part.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _built() -> dict:
    try:
        return json.loads(BUILT.read_text())
    except (OSError, ValueError):
        return {}


def _record(name: str, fp: str):
    BIN.mkdir(parents=True, exist_ok=True)
    tmp = BUILT.with_name(f"built.json.{os.getpid()}")
    tmp.write_text(json.dumps({**_built(), name: fp}, indent=2))
    os.replace(tmp, BUILT)


def _current(name: str, program: Path, fp: str) -> bool:
    return program.exists() and _built().get(name) == fp


def _run(cmd: list[str], what: str):
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"couldn't build {what} ({cmd[0]}): " + (done.stderr or "")[-400:])


def _compile(source: Path, program: Path, name: str, what: str):
    """swiftc to a temporary name, then in place of the old program."""
    BIN.mkdir(parents=True, exist_ok=True)
    tmp = program.with_name(f".{program.name}.{os.getpid()}")
    try:
        _run(["xcrun", "swiftc", "-O", str(source), "-o", str(tmp)], what)
        os.replace(tmp, program)
    finally:
        tmp.unlink(missing_ok=True)


def _helper_fp(source: Path) -> str:
    return fingerprint(source)


def _app_fp(source: Path, icon: Path) -> str:
    return fingerprint(source, icon, plistlib.dumps(APP_INFO, sort_keys=True))


def helper_built(helper: Path = HELPER, source: Path = HELPER_SOURCE) -> bool:
    """Whether the helper is built from its current source (it isn't in git)."""
    return _current("audio_helper", helper, _helper_fp(source))


def app_built(app: Path = APP, launcher: Path = LAUNCHER, source: Path = LAUNCHER_SOURCE, icon: Path = APP_ICON) -> bool:
    """Whether Voice Assistant.app is built from its current source, icon and Info.plist."""
    return _current("app", launcher, _app_fp(source, icon)) and (app / "Contents" / "Info.plist").exists()


def build_helper(helper: Path = HELPER, source: Path = HELPER_SOURCE) -> bool:
    """Builds the audio helper unless it's built from its current source: -> whether it built it."""
    if helper_built(helper, source):
        return False
    try:
        _compile(source, helper, "audio_helper", "the audio helper")
    except RuntimeError as e:
        raise RuntimeError(f"{e}; it needs Xcode's command line tools (xcode-select --install)") from None
    _record("audio_helper", _helper_fp(source))
    return True


def build_app(app: Path = APP, source: Path = LAUNCHER_SOURCE, icon: Path = APP_ICON) -> bool:
    """Builds Voice Assistant.app unless it's built from its current source: -> whether it built it.
    Built beside the old one, signed, then swapped in."""
    launcher_in = lambda bundle: bundle / "Contents" / "MacOS" / APP_INFO["CFBundleExecutable"]  # noqa: E731
    if app_built(app, launcher_in(app), source, icon):
        return False
    new = app.with_name(f".{app.name}.{os.getpid()}")
    old = app.with_name(f".{app.name}.old.{os.getpid()}")
    shutil.rmtree(new, ignore_errors=True)
    try:
        launcher_in(new).parent.mkdir(parents=True)
        (new / "Contents" / "Resources").mkdir()
        (new / "Contents" / "Info.plist").write_bytes(plistlib.dumps(APP_INFO))
        shutil.copyfile(icon, new / "Contents" / "Resources" / icon.name)
        _run(["xcrun", "swiftc", "-O", str(source), "-o", str(launcher_in(new))], "Voice Assistant.app")
        _run(["codesign", "--force", "--sign", "-", str(new)], "Voice Assistant.app")  # ad hoc: the bundle needs a signature
        if app.exists():
            app.rename(old)
        new.rename(app)
    finally:
        shutil.rmtree(new, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
    _record("app", _app_fp(source, icon))
    return True


def build_onboarding(program: Path = ONBOARDING, source: Path = ONBOARDING_SOURCE) -> bool:
    """Builds the first run's window unless it's built from its current source: -> whether it built it."""
    if _current("onboarding", program, fingerprint(source)):
        return False
    _compile(source, program, "onboarding", "its setup window")
    _record("onboarding", fingerprint(source))
    return True
