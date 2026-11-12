"""A talker that can't start (no model): when its sign-in check fails, it says why and ends voice with a fatal
error, instead of staying on and never answering; a reply meanwhile does nothing rather than raise."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from pipecat.frames.frames import StartFrame
from pipecat.processors.frame_processor import FrameDirection

import failure
import talker

failure.FILE = Path(tempfile.mkdtemp()) / "failure"  # not your real one
results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


async def started(raise_with) -> tuple[talker.Talker, list]:
    failure.clear()
    t, errors = talker.Talker(), []
    t.push_frame = lambda *a, **k: asyncio.sleep(0)
    t.create_task = lambda coro, *a, **k: (coro.close(), None)[1]  # no pipeline here: its tasks never run

    async def push_error(msg, exception=None, fatal=False, **k):
        errors.append((msg, fatal))

    t.push_error = push_error
    real = talker.make_options
    talker.make_options = raise_with
    try:
        with redirect_stdout(io.StringIO()):
            await t.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
    finally:
        talker.make_options = real
    return t, errors


def not_signed_in(listener=False):
    failure.record(failure.SIGN_IN)
    raise SystemExit("Not signed in to Claude Code. Refusing to start.")


def broken(listener=False):
    raise RuntimeError("claude didn't answer")


async def main():
    t, errors = await started(not_signed_in)
    ok(len(errors) == 1 and errors[0][1], f"not signed in: a fatal error ends voice ({errors})")
    said = failure.read()
    ok(bool(said) and said["why"] == failure.SIGN_IN and said["final"], f"the sign-in reason is kept for the mod ({said})")
    ok(t._client is None, "no conversation is left half made")
    try:
        await t._reply("hello")
        ok(True, "a reply with no conversation does nothing")
    except Exception as e:
        ok(False, f"a reply with no conversation raised {type(e).__name__}: {e}")

    t, errors = await started(broken)
    said = failure.read()
    ok(len(errors) == 1 and errors[0][1], "another failure to start: a fatal error too")
    ok(bool(said) and said["final"] and "RuntimeError" in said["why"], f"and a reason is recorded ({said})")
    failure.clear()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
raise SystemExit(0 if all(results) else 1)
