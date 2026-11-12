"""setup.py and the audio app's build (no models, no network, no Swift): a failed download ends in NOT READY with
its reason and no traceback, the login is checked before anything downloads, and voice's programs are built
once into ~/.voice-assistant/bin, kept through an update, and rebuilt only when their source changes."""

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

# The programs voice builds (builds.py), in a stand-in ~/.voice-assistant/bin with swiftc and codesign faked:
# built once, left alone by an update (the same source, in a new folder with new dates), rebuilt when the
# source or the app's Info.plist (its bundle id) changes.
import builds  # noqa: E402

ok(builds.APP_INFO["CFBundleIdentifier"] == "glass.labs.voice-assistant", "the app's bundle id is glass.labs.voice-assistant")
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    real = (builds.BIN, builds.BUILT, builds.subprocess.run, dict(builds.APP_INFO))
    builds.BIN = tmp / "bin"
    builds.BUILT = builds.BIN / "built.json"
    app, helper = builds.BIN / "Voice Assistant.app", builds.BIN / "audio_helper"
    plugin = tmp / "0.1.0"
    plugin.mkdir()
    source, icon, helper_source = plugin / "launcher.swift", plugin / "VoiceAssistant.icns", plugin / "main.swift"
    source.write_text("// launcher")
    icon.write_bytes(b"icon")
    helper_source.write_text("// helper")
    ran = []

    def fake_run(cmd, **_):
        ran.append(cmd[0])
        if cmd[0] == "xcrun":
            Path(cmd[-1]).write_text("built")
        return type("Done", (), {"returncode": 0, "stderr": ""})()

    builds.subprocess.run = fake_run
    try:
        build = lambda: (builds.build_app(app, source, icon), builds.build_helper(helper, helper_source))  # noqa: E731
        ok(build() == (True, True) and ran == ["xcrun", "codesign", "xcrun"], f"the first start builds both ({ran})")
        ok(plistlib.loads((app / "Contents" / "Info.plist").read_bytes()) == builds.APP_INFO and helper.read_text() == "built",
           "into ~/.voice-assistant/bin, with the app's Info.plist")
        ok(not [p for p in builds.BIN.iterdir() if p.name.startswith(".")], "and nothing half-built is left beside them")
        ran.clear()
        # An update: the same files in a new folder, all with new dates.
        update = tmp / "0.2.0"
        update.mkdir()
        for f in (source, icon, helper_source):
            (update / f.name).write_bytes(f.read_bytes())
            os.utime(update / f.name, (time.time() + 60, time.time() + 60))
        source, icon, helper_source = update / source.name, update / icon.name, update / helper_source.name
        ok(build() == (False, False) and ran == [], "an update with the same source builds nothing (the microphone permission stays)")
        builds.APP_INFO["CFBundleIdentifier"] = "dev.voice-assistant.app"
        ok(build() == (True, False) and ran == ["xcrun", "codesign"], f"a new bundle id rebuilds the app, and only it ({ran})")
        ran.clear()
        helper_source.write_text("// helper, changed")
        ok(build() == (False, True) and ran == ["xcrun"], f"a changed helper source rebuilds the helper ({ran})")
    finally:
        builds.BIN, builds.BUILT, builds.subprocess.run = real[:3]
        builds.APP_INFO.clear()
        builds.APP_INFO.update(real[3])

print(f"\n{sum(results)}/{len(results)} passed")
raise SystemExit(0 if all(results) else 1)
