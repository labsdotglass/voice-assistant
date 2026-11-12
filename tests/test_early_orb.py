"""The orb comes up before the daemon's heavy imports, and AudioHelper adopts that helper (no models, no mic).

early_orb.start() runs first thing in daemon.py, so the orb (and its spinner) is on screen within a second
instead of after 4-10 s of imports. This check starts a stand-in helper the same way (a script that says
it's ready on stderr, then streams "mic" bytes on stdout and echoes stdin back to a file), lets its mic fill
the pipe for a while (the drain must keep it moving), then has AudioHelper.ensure_started adopt it: the
same pid, still our child, its ready line read, and what AudioHelper writes reaches it. It also checks
when an early start is wanted: not on a restart in place, a first run, --no-aec or an orb that's off.
"""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import os
import tempfile
import time
from pathlib import Path

import early_orb

results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {msg}", flush=True)


def check_wanted():
    real = (early_orb.VOICEPRINT, early_orb.ORB_PAGE, early_orb.helper_built, early_orb.app_built)
    with tempfile.TemporaryDirectory() as tmp:
        print_ = Path(tmp) / "voiceprint.npy"
        print_.write_bytes(b"x")
        early_orb.VOICEPRINT, early_orb.ORB_PAGE = print_, print_
        early_orb.helper_built = early_orb.app_built = lambda: True
        argv = ["daemon.py", "--socket", "x"]
        ok(early_orb.wanted(argv, {}), "an ordinary start brings the orb up early")
        ok(not early_orb.wanted(argv + ["--no-aec"], {}), "not with --no-aec (no helper at all)")
        ok(not early_orb.wanted(argv, {"VOICE_ORB": "0"}), "not when the orb is off")
        ok(not early_orb.wanted(argv, {"VOICE_HANDOFF": '{"helper": {"pid": 1}}'}), "not on a restart in place: its orb never went away")
        ok(not early_orb.wanted(argv, {"VOICE_FIRST_TOUR": "1"}), "not in a first run: the onboarding window shows its own orb")
        early_orb.helper_built = lambda: False
        ok(not early_orb.wanted(argv, {}), "not when the helper needs building: the daemon builds it first")
        early_orb.helper_built = lambda: True
        print_.unlink()
        ok(not early_orb.wanted(argv, {}), "not without a voice print (a first run)")
    early_orb.VOICEPRINT, early_orb.ORB_PAGE, early_orb.helper_built, early_orb.app_built = real


async def check_adopt(tmp: Path):
    from helper_transport import AudioHelper, _alive_child

    got = tmp / "stdin"
    # The stand-in: ready on stderr, then far more "mic" than a pipe holds (64 KB), then echo stdin to a file.
    stand_in = tmp / "helper.sh"
    stand_in.write_text(
        "#!/bin/sh\n"
        "echo 'audio_helper: ready: mic 16000Hz (stand-in)' >&2\n"
        "head -c 400000 /dev/zero\n"
        f"cat > '{got}'\n")
    stand_in.chmod(0o755)
    early_orb.LAUNCHER = stand_in
    early_orb.wanted = lambda argv, env: True
    began = time.monotonic()
    ok(early_orb.start(), "start() starts the helper")
    took = time.monotonic() - began
    ok(took < 0.5, f"and returns at once ({took * 1000:.0f} ms): the imports aren't held up")
    time.sleep(1.0)  # the daemon's imports: the mic keeps writing meanwhile

    helper = AudioHelper(out_rate=24000)
    helper._has_orb = True
    pid = early_orb._early["pid"]
    await asyncio.wait_for(helper.ensure_started(), 5)
    ok(helper.proc is not None and helper.proc.pid == pid, f"AudioHelper adopted the early helper (pid {pid}), not a new one")
    ok(_alive_child(pid), "it's still our child")
    ok(early_orb._early is None, "take() hands it on once")
    helper.proc.stdin.write(b"hello")
    await helper.proc.stdin.drain()
    helper.proc.stdin.close()
    code = await asyncio.wait_for(helper.proc.wait(), 5)
    ok(code == 0, f"the helper never stalled on a full pipe: its mic was drained, and it ran to the end (exit {code})")
    ok(got.exists() and got.read_bytes().endswith(b"hello"), "what AudioHelper writes reaches it")
    ok(early_orb.take(24000) is None, "nothing to take a second time")


def main():
    check_wanted()
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(check_adopt(Path(tmp)))
    print(f"\n{sum(results)}/{len(results)} passed")
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
