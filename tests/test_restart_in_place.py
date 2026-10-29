"""A restart in place keeps the orb's helper and the inbox's socket (no models, no mic).

The daemon's language switch re-executes it (exec) instead of exiting, handing on the audio helper's pipes
and the listening socket. This check does the same with a stand-in helper (`cat`, which echoes) and a real
Unix socket: before exec it hands both off; after exec, in the same process, it adopts the helper (writes
reach it and its output comes back, same pid, still our child) and serves the socket. A client that
connected during the exec gets its answer instead of finding nothing there.
"""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STAGE = "RESTART_TEST_STAGE"
results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {msg}", flush=True)


async def before(tmp: Path):
    """Stage 1: start the helper and the socket, as the daemon has them, then re-execute."""
    from helper_transport import HANDOFF_ENV, AudioHelper

    helper = AudioHelper(out_rate=24000)
    helper.proc = await asyncio.create_subprocess_exec(
        "cat", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(tmp / "inbox.sock"))
    sock.listen()
    # A session asking while voice restarts: it connects now and waits for its answer.
    client = subprocess.Popen([sys.executable, "-c", (
        "import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);s.sendall(b'hello');"
        "open(sys.argv[2],'w').write(s.recv(100).decode())"), str(tmp / "inbox.sock"), str(tmp / "answer")])
    time.sleep(0.5)  # it's connected (the kernel holds it in the socket's backlog)
    handed = {"helper": helper.hand_off(), "socket": sock.fileno(), "pid": os.getpid(), "client": client.pid}
    sock.set_inheritable(True)
    os.environ[HANDOFF_ENV] = json.dumps(handed)
    os.environ[STAGE] = "after"
    os.environ["RESTART_TEST_DIR"] = str(tmp)
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])


async def after(tmp: Path):
    """Stage 2, the same process after exec: adopt the helper, serve the socket."""
    from helper_transport import HANDOFF, AdoptedHelper, _alive_child

    handed = HANDOFF
    ok(handed.get("pid") == os.getpid(), f"the same process after exec (pid {os.getpid()})")
    helper = handed.get("helper") or {}
    ok(_alive_child(helper.get("pid", -1)), f"the helper outlived the exec and is still our child (pid {helper.get('pid')})")
    proc = await AdoptedHelper.adopt(helper)
    proc.stdin.write(b"orb state\n")
    await proc.stdin.drain()
    echoed = await asyncio.wait_for(proc.stdout.readline(), 5)
    ok(echoed == b"orb state\n", f"writes reach the adopted helper and its output comes back: {echoed!r}")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, fileno=handed["socket"])
    conn, _ = sock.accept()
    conn.sendall(b"pong:" + conn.recv(100))
    conn.close()
    for _ in range(50):
        if (tmp / "answer").exists() and (tmp / "answer").read_text():
            break
        await asyncio.sleep(0.1)
    answer = (tmp / "answer").read_text() if (tmp / "answer").exists() else ""
    ok(answer == "pong:hello", f"a client that connected during the exec got its answer: {answer!r}")

    proc.stdin.close()
    code = await asyncio.wait_for(proc.wait(), 5)
    ok(code == 0, f"the adopted helper is waited for like our own child (exit {code})")
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    if os.environ.get(STAGE) == "after":
        asyncio.run(after(Path(os.environ["RESTART_TEST_DIR"])))
    else:
        asyncio.run(before(Path(tempfile.mkdtemp())))
