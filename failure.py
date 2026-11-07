"""Why voice can't run, in one plain line and the one thing to do, left for the mod (hooks/register.tsx) to show.

final: starting again won't help until you've done something, so the mod doesn't try. Standard library only: the
first run's window (onboarding.py) imports it before anything else is set up.
"""

import json
import subprocess

from home import RUN, private

FILE = RUN / "failure"  # the mod reads and removes it (paths, takeFailure)

DEV_TOOLS = "Voice needs Apple's developer tools. Install them (a system dialog opens), then try again."
MICROPHONE = "Voice isn't allowed to use the microphone. Turn it on in System Settings, which just opened, then try again."
AUDIO = "Voice couldn't start its audio. Check your microphone and speakers, then try again."
SIGN_IN = "Voice needs you to sign in to Claude Code. Sign in, then try again."
SUBSCRIPTION = "Voice runs on your Claude subscription, and Claude Code is signed in another way. Sign in with your claude.ai account, then try again."


def record(why: str, final: bool = True):
    private(FILE.parent)
    FILE.write_text(json.dumps({"why": why, "final": final}))


def read() -> dict | None:
    try:
        said = json.loads(FILE.read_text())
    except (OSError, ValueError):
        return None
    return said if isinstance(said, dict) and said.get("why") else None


def clear():
    FILE.unlink(missing_ok=True)


def dev_tools_missing() -> bool:
    return subprocess.run(["xcode-select", "-p"], capture_output=True).returncode != 0


def install_dev_tools():
    subprocess.Popen(["xcode-select", "--install"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_microphone_settings():
    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cant_build(what: str) -> str:
    """What to say when a build with Apple's tools failed; opens their installer when they're what's missing."""
    if dev_tools_missing():
        install_dev_tools()
        return DEV_TOOLS
    return f"Voice couldn't build {what}. Update Apple's developer tools, then try again."
