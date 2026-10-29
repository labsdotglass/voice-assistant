"""The first run's window: language, setup progress, the tour and learning your voice, in a window of its own.

    uv run --no-project onboarding.py [--replay] [--enroll]
    uv run --no-project onboarding.py --install-failed <reason> | --install-retry   (the mod's, no window)

Builds bin/onboarding when its source changed, opens the window and streams its events, one line each,
to the mod: "start" (a language was chosen and saved, the state file says "installing": start voice),
"later" (dismissed before choosing a language), "joined" (a window is already open: no second one), "hidden", "shown", "done" and "closed" (the window is gone),
passed through. The window stays open for the whole first run; it talks to the daemon through the state
and request files (first_run.py). Once you've chosen a language in it, ~/.voice-assistant/onboarded keeps it from showing
again (/voice-tour shows it again, with --replay). Standard library only, so it opens at once, before the
first start's long setup. VOICE_ONBOARDING_PROGRAM runs another program in the window's place (the checks).
"""

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import first_run
from home import HOME

ROOT = Path(__file__).parent
SOURCE = ROOT / "audio_helper" / "onboarding.swift"
PROGRAM = ROOT / "bin" / "onboarding"
PAGE = ROOT / "orb-page" / "dist" / "onboarding.html"
SEEN = HOME / "onboarded"
LANGUAGE_FILE = ROOT / "languages.json"
TOUR_OPEN = HOME / "tour-open"  # the window holds its pid here while it's open


def languages() -> list[dict]:
    """The languages in the menu's order (experimental ones last), from languages.json: code, label, native name, download_gb."""
    every = json.loads(LANGUAGE_FILE.read_text())["languages"]
    return sorted(every, key=lambda l: l["experimental"])  # sorted is stable: the rest keep their order


def with_current(langs: list[dict]) -> list[dict]:
    """The languages, the one voice is set to (settings.json) marked current: shown again, the window speaks it."""
    try:
        saved = json.loads((HOME / "settings.json").read_text()).get("language")
    except (OSError, ValueError, AttributeError):
        saved = None
    return [{**l, "current": True} if l["code"] == saved else l for l in langs]


def build():
    if os.environ.get("VOICE_ONBOARDING_PROGRAM"):
        return
    if PROGRAM.exists() and PROGRAM.stat().st_mtime >= SOURCE.stat().st_mtime:
        return
    PROGRAM.parent.mkdir(exist_ok=True)
    done = subprocess.run(["xcrun", "swiftc", "-O", str(SOURCE), "-o", str(PROGRAM)], capture_output=True, text=True)
    if done.returncode:
        sys.exit("couldn't build the slideshow; it needs Xcode's command line tools (xcode-select --install): " + done.stderr[-400:])


def save_language(code: str):
    # settings.py's file (it checks the choice when it loads it); stdlib only, as this runs before setup.
    if not code.isalpha() or len(code) != 2:
        return
    file = HOME / "settings.json"
    try:
        current = json.loads(file.read_text())
    except (OSError, ValueError):
        current = {}
    file.parent.mkdir(exist_ok=True)
    file.write_text(json.dumps({**current, "language": code}, indent=2))


def window_is_open() -> bool:
    try:
        os.kill(int(TOUR_OPEN.read_text().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def install_failed(reason: str):
    """Voice's packages didn't install (the mod saw `uv run daemon.py` exit before it answered): the open window
    shows Couldn't install, and that /talk tries again; no daemon runs to read a Retry."""
    first_run.write("failed", reason=reason, kind="install")


def install_retry():
    """/talk again after Couldn't install: the window goes back to Setting up while the packages install."""
    try:
        state = json.loads(first_run.STATE.read_text())
    except (OSError, ValueError):
        return
    if isinstance(state, dict) and state.get("phase") == "failed" and state.get("kind") == "install":
        first_run.write("installing")


def main():
    if "--install-failed" in sys.argv:  # the mod's, around a start that never got going: no window
        return install_failed(" ".join(sys.argv[sys.argv.index("--install-failed") + 1:]).strip() or "uv couldn't set up voice's packages")
    if "--install-retry" in sys.argv:
        return install_retry()
    if window_is_open():  # a first run is already underway: a second /talk joins it, no second window
        print("joined", flush=True)
        return
    # Claim the file before the build (seconds, on a first run): the window overwrites it with its own pid.
    TOUR_OPEN.parent.mkdir(exist_ok=True)
    mine = str(os.getpid())
    TOUR_OPEN.write_text(mine)
    if not any(f in sys.argv for f in ("--replay", "--enroll")):
        # A new first run: what a crashed earlier one left behind would look like a language already chosen.
        for stale in (first_run.STATE, first_run.REQUEST):
            with contextlib.suppress(OSError):
                stale.unlink()
    try:
        run_window()
    finally:
        with contextlib.suppress(OSError):  # the window removes its own; remove a claim it never replaced
            if TOUR_OPEN.read_text().strip() == mine:
                TOUR_OPEN.unlink()


def run_window():
    build()
    program = os.environ.get("VOICE_ONBOARDING_PROGRAM") or str(PROGRAM)
    flags = [f for f in ("--replay", "--enroll") if f in sys.argv]
    window = subprocess.Popen([program, str(PAGE), *flags, "--languages", json.dumps(with_current(languages()))], stdout=subprocess.PIPE, text=True)
    chosen = ended = False
    for line in window.stdout:  # one event per line, as it happens
        line = line.strip()
        if line.startswith("language "):
            save_language(line.split(" ", 1)[1])
            first_run.write("installing")
            chosen = True
            line = "start"
        elif line == "later" and chosen:
            continue  # only valid before a language is chosen
        elif not line:
            continue
        ended = ended or line in ("later", "done", "closed")
        print(line, flush=True)
    window.wait()
    if chosen or flags:  # Not now before a language leaves the first run to come again on the next /talk
        SEEN.parent.mkdir(exist_ok=True)
        SEEN.touch()
    if not ended:  # the window went without saying so
        print("closed" if chosen else "later", flush=True)


if __name__ == "__main__":
    main()
