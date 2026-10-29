"""setup.py and the audio app's build (no models, no network, no Swift): a failed download ends in NOT READY with
its reason and no traceback, the login is checked before anything downloads, and an app built under the old
bundle id is rebuilt under the new one, once."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import contextlib
import io
import os
import plistlib
import tempfile
import time
import urllib.error
from pathlib import Path

import auto_mode
import helper_transport
import setup

results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def run_setup(**steps) -> tuple[str, int, list[str]]:
    """setup.main() and its report with these steps replaced: -> what it printed, its exit code, the steps' order."""
    order = []

    def as_step(name, then=None):
        def run(*_):
            order.append(name)
            return then() if callable(then) else then
        return run

    setup.problems.clear()
    setup.logged_in = as_step("login", steps.get("login", "claude.ai"))
    setup.build_apps = as_step("build", steps.get("build"))
    setup.shared_voice = as_step("shared", steps.get("shared"))
    setup.voice_check = as_step("ecapa", steps.get("ecapa"))
    setup.picked_language = lambda: None
    auto_mode.mode = lambda: "auto"  # no reading ~/.claude here
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        setup.main()
        code = setup.report()
    return out.getvalue(), code, order


def raise_(e):
    def go():
        raise e
    return go


text, code, order = run_setup()
ok(code == 0 and "READY." in text and "NOT READY" not in text, "every step fine: READY")
ok(order[0] == "login" and order.index("login") < order.index("shared"), f"the login is checked before any download ({order})")
ok("ffmpeg" not in text, "no ffmpeg note")

text, code, _ = run_setup(shared=raise_(urllib.error.URLError("nodename nor servname provided, or not known")))
ok(code == 1 and "NOT READY" in text, "a failed download: NOT READY, exit 1")
ok("couldn't download Kokoro's voice files: no connection" in text and "check your network" in text,
   "it says which download and why")
ok("Traceback" not in text, "no traceback")

text, code, order = run_setup(ecapa=raise_(OSError("Connection refused by huggingface.co")),
                              shared=raise_(urllib.error.URLError("timed out")))
ok(code == 1 and text.count("PROBLEM:") == 2 and "ecapa" in order, "two failures: both listed, the later steps still ran")

text, code, _ = run_setup(build=raise_(SystemExit("couldn't build the slideshow; it needs Xcode's command line tools")))
ok(code == 1 and "couldn't build the audio app" in text and "Traceback" not in text, "a failed build is a problem, not an exit")

text, code, _ = run_setup(login="console")
ok(code == 1 and "not logged in with a Claude subscription" in text, "not on the subscription: NOT READY")

# The bundle id: an app built under the old id is rebuilt (its Info.plist differs), then left alone.
ok(helper_transport.APP_INFO["CFBundleIdentifier"] == "glass.labs.voice-assistant", "the app's bundle id is glass.labs.voice-assistant")
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    ht = helper_transport
    ht.APP = tmp / "Voice Assistant.app"
    ht.LAUNCHER = ht.APP / "Contents" / "MacOS" / "voice-assistant"
    ht.LAUNCHER_SOURCE, ht.APP_ICON = tmp / "launcher.swift", tmp / "VoiceAssistant.icns"
    ht.LAUNCHER_SOURCE.write_text("// source")
    ht.APP_ICON.write_bytes(b"icon")
    ht.LAUNCHER.parent.mkdir(parents=True)
    ht.LAUNCHER.write_text("built")
    future = time.time() + 60
    os.utime(ht.LAUNCHER, (future, future))  # built after its sources: only the plist can call for a rebuild
    (ht.APP / "Contents" / "Info.plist").write_bytes(plistlib.dumps({**ht.APP_INFO, "CFBundleIdentifier": "dev.voice-assistant.app"}))
    ran = []

    class Done:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*cmd, **_):
        ran.append(cmd[0])
        return Done()

    real = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = fake_exec
    try:
        asyncio.run(ht.build_app())
        info = plistlib.loads((ht.APP / "Contents" / "Info.plist").read_bytes())
        ok(ran == ["xcrun", "codesign"] and info["CFBundleIdentifier"] == "glass.labs.voice-assistant",
           f"an app built under the old id is rebuilt and signed under the new one ({ran})")
        ran.clear()
        asyncio.run(ht.build_app())
        ok(ran == [], "then it's left alone")
    finally:
        asyncio.create_subprocess_exec = real

print(f"\n{sum(results)}/{len(results)} passed")
raise SystemExit(0 if all(results) else 1)
