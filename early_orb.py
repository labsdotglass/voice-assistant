"""The orb on screen within a second of a start, before the daemon's heavy imports.

Importing pipecat, torch and MLX takes about 4 s on a warm start and 10 s on a cold one, and until now the
audio helper (and its orb) started only after that: a start showed nothing at all for that long. So the
daemon calls start() first, before those imports. The orb page shows its spinner while voice is starting.
helper_transport's AudioHelper adopts the helper with take(), as it adopts one handed on by a restart in place.

Only an ordinary start does this: not a restart in place (its orb never went away), not a first run (the
onboarding window shows its own orb). When the helper or Voice Assistant.app needs building (the first start
after an update that changed their source), start() builds them first: a few seconds, still well before the
imports are done. step() tells the mod how far the start has got. Standard library only.
"""

import json
import os
import select
import sys
import threading
import time
from pathlib import Path

import builds
import home
from builds import HELPER, LAUNCHER  # in ~/.voice-assistant/bin, kept across updates

HERE = Path(__file__).parent
ORB_PAGE = HERE / "orb-page" / "dist" / "orb.html"
OUT_RATE = 24000  # Kokoro's: the rate the daemon's HelperAudioTransport asks for
VOICEPRINT = home.HOME / "voiceprint.npy"  # speaker.VOICEPRINT: without it, this start is a first run

_early: dict | None = None  # the helper started here, until take()
_draining = threading.Event()


def wanted(argv: list[str], env) -> bool:
    """Whether this start brings the orb up early (see the module's docstring)."""
    return ("--no-aec" not in argv
            and env.get("VOICE_ORB", "1") == "1"
            and '"helper"' not in env.get("VOICE_HANDOFF", "")  # a restart in place hands its helper on
            and env.get("VOICE_FIRST_TOUR") != "1"
            and VOICEPRINT.exists()
            and ORB_PAGE.exists())


def _drain(fd: int):
    """Reads the mic and throws it away until the daemon takes the helper: a full pipe would stall its audio."""
    while not _draining.is_set():
        try:
            if select.select([fd], [], [], 0.05)[0] and not os.read(fd, 65536):
                return  # the helper is gone
        except OSError:
            return


def step(name: str):
    """Tells the mod how far this start has got, for its panel: "loading" (the imports), "building" (the
    helper and its app), "models" (the speech models, from the daemon). Its stdout, as talker.emit writes."""
    sys.stdout.write("@@VOICE " + json.dumps({"type": "starting", "step": name}) + "\n")
    sys.stdout.flush()


def start() -> bool:
    """Starts the audio helper, and so the orb, now, when this start wants it: -> whether it did."""
    global _early
    if _early or not wanted(sys.argv, os.environ):
        return False
    try:
        # Built here, if they need it (after an update that changed their source): seconds, and then the orb
        # comes up before the imports as on any start. If a build fails, the daemon builds them again and says why.
        if not (builds.helper_built() and builds.app_built()):
            step("building")
            builds.build_helper()
            builds.build_app()
    except (OSError, RuntimeError):
        return False
    try:
        (in_r, in_w), (out_r, out_w), (err_r, err_w) = os.pipe(), os.pipe(), os.pipe()
        pid = os.posix_spawn(
            str(LAUNCHER),
            [str(LAUNCHER), str(HELPER), "--out-rate", str(OUT_RATE), "--orb", str(ORB_PAGE)],
            os.environ,
            file_actions=[(os.POSIX_SPAWN_DUP2, in_r, 0), (os.POSIX_SPAWN_DUP2, out_w, 1), (os.POSIX_SPAWN_DUP2, err_w, 2),
                          *((os.POSIX_SPAWN_CLOSE, fd) for fd in (in_r, in_w, out_r, out_w, err_r, err_w))],
        )
    except OSError:
        return False  # the daemon starts it the usual way, and says why if it can't
    for fd in (in_r, out_w, err_w):
        os.close(fd)
    _draining.clear()  # a helper taken before (and so a set event) mustn't stop this one's drain
    drain = threading.Thread(target=_drain, args=(out_r,), daemon=True, name="early-orb-drain")
    drain.start()
    _early = {"pid": pid, "stdin": in_w, "stdout": out_r, "stderr": err_r, "drain": drain, "at": time.monotonic()}
    return True


def take(out_rate: int) -> dict | None:
    """The helper start() started, for AudioHelper to adopt (its pid and pipe fds), once: None if there's none
    or it was started for another rate (it's stopped then, and AudioHelper starts its own)."""
    global _early
    early, _early = _early, None
    if not early:
        return None
    _draining.set()
    early.pop("drain").join(1)  # its select times out within 50 ms: it stops reading before AudioHelper starts
    if out_rate != OUT_RATE:
        os.close(early["stdin"])  # EOF: the helper leaves
        return None
    return early
