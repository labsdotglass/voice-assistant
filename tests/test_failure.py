"""Why voice can't run, without the model: each known failure leaves one plain line for the mod to show, marked final so
the mod doesn't start voice again, and the first run's window says why it couldn't be built instead of showing nothing."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import claude_login
import failure
import helper_transport
import onboarding

results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def login(stdout: str) -> dict | None:
    failure.clear()
    real = subprocess.run
    subprocess.run = lambda *a, **k: SimpleNamespace(stdout=stdout, returncode=0)
    try:
        claude_login.ensure_subscription_auth()
    except SystemExit:
        pass
    except Exception as e:
        return {"why": f"raised {type(e).__name__}", "final": False}
    finally:
        subprocess.run = real
    return failure.read()


def logins():
    for out, what in [("", "empty output"), ("not json", "output that isn't JSON"), ('{"loggedIn": false}', "no login")]:
        said = login(out)
        ok(bool(said) and said["final"] and "sign in" in said["why"].lower(), f"{what}: says to sign in ({said})")
    said = login('{"authMethod": "api_key"}')
    ok(bool(said) and said["final"] and "subscription" in said["why"], f"another way of signing in: says what voice needs ({said})")
    ok(login('{"authMethod": "claude.ai"}') is None, "on the subscription: nothing recorded")


def built(tools_missing: bool) -> tuple[str, list]:
    """onboarding.build() with swiftc failing: what it prints, and what it ran to install the tools."""
    installs, out = [], io.StringIO()
    real_missing, real_install, real_run, real_env = failure.dev_tools_missing, failure.install_dev_tools, subprocess.run, onboarding.os.environ.pop("VOICE_ONBOARDING_PROGRAM", None)
    real_program = onboarding.PROGRAM
    failure.dev_tools_missing = lambda: tools_missing
    failure.install_dev_tools = lambda: installs.append(True)
    subprocess.run = lambda *a, **k: SimpleNamespace(returncode=1, stderr="xcrun: error", stdout="")
    with tempfile.TemporaryDirectory() as tmp:
        onboarding.PROGRAM = Path(tmp, "no-such-program")
        try:
            with redirect_stdout(out):
                onboarding.build()
        except SystemExit:
            pass
    failure.dev_tools_missing, failure.install_dev_tools, subprocess.run, onboarding.PROGRAM = real_missing, real_install, real_run, real_program
    if real_env:
        onboarding.os.environ["VOICE_ONBOARDING_PROGRAM"] = real_env
    return out.getvalue(), installs


def window():
    said, installs = built(True)
    ok(said.startswith("failed ") and "developer tools" in said, f"the window needs the tools: the mod is told why ({said.strip()!r})")
    ok(installs == [True], "and Apple's installer is opened")
    said, installs = built(False)
    ok(said.startswith("failed ") and failure.DEV_TOOLS not in said and not installs, f"another build failure doesn't blame the tools ({said.strip()!r})")


async def started(line: bytes = b"", build_fails: bool = False) -> tuple[dict | None, bool, list]:
    """HelperAudioTransport's helper starting, with the build failing or the helper saying `line` first."""
    failure.clear()
    opened = []
    real = (helper_transport.build_helper, helper_transport.build_app, asyncio.create_subprocess_exec, failure.open_microphone_settings)

    async def fine():
        return None

    async def broken():
        raise RuntimeError("couldn't build the audio helper")

    async def spawn(*a, **k):
        async def readline():
            return line

        return SimpleNamespace(stderr=SimpleNamespace(readline=readline), returncode=1, pid=1, kill=lambda: None)

    helper_transport.build_helper, helper_transport.build_app = (broken if build_fails else fine), fine
    asyncio.create_subprocess_exec = spawn
    failure.open_microphone_settings = lambda: opened.append(True)
    raised = False
    try:
        await helper_transport.AudioHelper(out_rate=24000).ensure_started()
    except RuntimeError:
        raised = True
    helper_transport.build_helper, helper_transport.build_app, asyncio.create_subprocess_exec, failure.open_microphone_settings = real
    return failure.read(), raised, opened


async def helper():
    real = failure.dev_tools_missing
    failure.dev_tools_missing = lambda: True
    said, raised, _ = await started(build_fails=True)
    ok(raised and bool(said) and said["final"] and "developer tools" in said["why"], f"the helper can't be built, no tools: says so ({said})")
    failure.dev_tools_missing = lambda: False
    said, raised, _ = await started(build_fails=True)
    ok(raised and bool(said) and said["final"] and said["why"] != failure.DEV_TOOLS, f"the helper can't be built, tools there: says it plainly ({said})")
    failure.dev_tools_missing = real

    said, raised, opened = await started(b"audio_helper: microphone not allowed")
    ok(raised and bool(said) and said["final"] and "microphone" in said["why"].lower() and opened == [True],
       f"the microphone isn't allowed: says so and opens System Settings ({said})")
    said, raised, opened = await started(b"audio_helper: audio failed to start: nope")
    ok(raised and bool(said) and said["final"] and not opened, f"audio doesn't start: says so ({said})")


def record():
    failure.clear()
    ok(failure.read() is None, "nothing recorded: nothing to read")
    failure.record("It crashed.", final=False)
    ok(failure.read() == {"why": "It crashed.", "final": False}, "a crash is recorded, and voice may start again")
    failure.FILE.write_text("not json")
    ok(failure.read() is None, "a broken file reads as nothing")
    failure.clear()
    failure.clear()
    ok(failure.read() is None, "clearing twice is fine")


with tempfile.TemporaryDirectory() as tmp:
    failure.FILE = Path(tmp, "failure")
    record()
    logins()
    window()
    asyncio.run(helper())
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
