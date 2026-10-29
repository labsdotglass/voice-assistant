"""Checks that voice mode can run on this Mac and gets ahead of its first start: optional, as the first /talk
does all of this itself (its window shows the downloads).

    uv run setup.py
    uv run setup.py --auto-mode   # only turns on auto mode for new sessions (keeps a copy of your settings)

Run it from the plugin's folder. Safe to run again: every step skips what's already done. The Claude login is
checked first, as it's quick; then it builds the audio app and the first-run window and downloads the shared
voice files and the voice check's model, and your language's models once you've picked one (before that, the
first start downloads them, for the language you pick). A step that fails (no network, say) is listed with its
reason under NOT READY, and the rest still run.
"""

import asyncio
import json
import platform
import subprocess
import sys
import time
import urllib.error

problems: list[str] = []


def step(name: str):
    print(f"\n== {name}", flush=True)
    return time.monotonic()


def done(t0: float, note: str = "ok"):
    print(f"   {note} ({time.monotonic() - t0:.0f} s)", flush=True)


def fail(msg: str):
    problems.append(msg)
    print(f"   PROBLEM: {msg}", flush=True)


def reason(e: BaseException) -> str:
    """Why a step failed, in a line: a network failure says so, without the traceback."""
    if isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError):
        return f"no connection ({e.reason}); check your network, then run this again"
    text = str(e).strip().splitlines()
    line = text[-1] if text else type(e).__name__
    if any(w in line.lower() for w in ("connection", "resolve", "network", "timed out", "offline", "internet")):
        return f"{line[:300]}; check your network, then run this again"
    return line[:300]


def attempt(t0: float, what: str, action, note: str = "ok"):
    """Runs one step: done, or a problem with its reason; never a traceback."""
    try:
        action()
    except (Exception, SystemExit) as e:  # onboarding.build exits with its reason
        fail(f"couldn't {what}: {reason(e)}")
    else:
        done(t0, note)


def logged_in() -> str | None:
    """How the Claude CLI voice runs is signed in (the talker's own check, claude_login): -> its authMethod."""
    from pathlib import Path

    import claude_agent_sdk

    cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    out = subprocess.run([str(cli), "auth", "status"], capture_output=True, text=True, timeout=60).stdout
    return (json.loads(out) if out.strip().startswith("{") else {}).get("authMethod")


def build_apps():
    from helper_transport import build_app, build_helper

    import onboarding

    async def both():
        await build_helper()
        await build_app()

    asyncio.run(both())
    onboarding.build()


def picked_language() -> str | None:
    import settings

    try:
        return json.loads(settings.FILE.read_text()).get("language")
    except (OSError, ValueError, AttributeError):
        return None


def language_models():
    import settings
    from hebrew_voice import load_fast_hebrew, load_fast_kokoro
    from interim import load_speech_models, set_draft_language

    chosen = settings.load()
    set_draft_language(chosen["language"])
    final = settings.language(chosen["language"])[4] or "mlx-community/whisper-large-v3-mlx"  # as daemon.hearing
    load_speech_models(final, [load_fast_hebrew if chosen["voice"] == "he_shaul" else load_fast_kokoro], strict=True)


def shared_voice():
    import first_run

    first_run.fetch_shared()  # whole files only: a cut-off download is fetched again


def voice_check():
    from speaker import VoicePrint

    VoicePrint()


def main():
    t = step("This Mac")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        fail(f"voice mode needs a Mac with Apple silicon; this is {platform.system()} {platform.machine()}")
        return
    done(t, f"macOS {platform.mac_ver()[0]}, Apple silicon")

    t = step("Claude login (voice runs on your Claude subscription)")
    try:
        method = logged_in()
    except Exception as e:
        fail(f"couldn't check the Claude login: {reason(e)}")
    else:
        if method == "claude.ai":
            done(t, "logged in with a Claude subscription")
        else:
            fail(f"not logged in with a Claude subscription (found: {method!r}); sign in with `claude auth login`, "
                 "choosing your Claude account, then run this again")

    t = step("Xcode command line tools: the audio app and the first-run window")
    if subprocess.run(["xcrun", "--find", "swiftc"], capture_output=True).returncode:
        fail("Xcode's command line tools are missing: run `xcode-select --install`, accept the dialog, then run this again")
    else:
        attempt(t, "build the audio app", build_apps, "built: bin/Voice Assistant.app, bin/audio_helper, bin/onboarding")

    # Every language's: Kokoro's ONNX voice (the fallback voice) and the voice check's ECAPA.
    import first_run

    mb = sum(size for _, size in first_run.shared_files().values()) / 1e6
    t = step(f"Voice files: Kokoro ONNX ({mb:.0f} MB, the first time)")
    attempt(t, "download Kokoro's voice files", shared_voice)

    t = step("Speaker model: ECAPA (answers only your voice)")
    attempt(t, "download the voice check's model", voice_check)

    # Only your language's models (languages.json lists them): before you've picked one, the first start
    # downloads them, with a progress bar in its window.
    picked = picked_language()
    if not picked:
        t = step("Speech models")
        done(t, "not yet: voice's first start downloads them, for the language you pick")
    else:
        size = next((l["download_gb"] for l in json.loads((first_run.ROOT / "languages.json").read_text())["languages"]
                     if l["code"] == picked), None)
        t = step(f"Speech models for {picked}: Whisper, the draft model and the voice"
                 + (f" (up to {size} GB in all, the first time)" if size else ""))
        attempt(t, "download the speech models", language_models)

    t = step("Auto mode for new sessions (voice can't answer permission prompts)")
    import auto_mode

    if auto_mode.mode() == "auto":
        done(t, "on")
    else:
        done(t, f"off ({auto_mode.mode()}): sessions you start by voice stop at permission prompts. "
                "Turn it on with `uv run setup.py --auto-mode`")


def report() -> int:
    print("\n" + ("=" * 60))
    if problems:
        print("NOT READY. Fix these, then run `uv run setup.py` again:")
        for p in problems:
            print(f" - {p}")
        return 1
    print("READY. In the Claude app, type /talk in any session (or press Start voice under the prompt);\n"
          "its window takes you through the rest. What's left by hand is in the README's Install section.")
    return 0


if __name__ == "__main__":
    if "--auto-mode" in sys.argv:  # the person's own choice: only this, and nothing else runs
        import auto_mode

        print(auto_mode.turn_on())
        sys.exit(0)
    main()
    sys.exit(report())
