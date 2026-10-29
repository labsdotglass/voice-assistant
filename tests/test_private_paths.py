"""Voice's runtime files and logs (no model): in ~/.voice-assistant, readable only by you, never in the shared /tmp."""

import _root  # noqa: F401  (the repo root on the import path)
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import home
import new_session
import talker

ROOT = Path(__file__).resolve().parent.parent
results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


folder = home.private(Path(tempfile.mkdtemp()) / "a" / "run")
ok(folder.is_dir() and folder.stat().st_mode & 0o777 == 0o700, "private() makes a missing folder, 0700")
folder.chmod(0o755)
ok(home.private(folder).stat().st_mode & 0o777 == 0o700, "private() tightens a folder that's already there")

paths = [home.SOCKET, home.LOG, new_session.FINISHED_DIR, new_session.WAITING_DIR, talker.RESTART_MARKER]
ok(all(p.is_relative_to(home.HOME) for p in paths), "socket, log, note folders and restart marker are all under ~/.voice-assistant")
ok(len(str(home.SOCKET).encode()) < 104, "the socket's path fits macOS's limit for a Unix socket")

# The mod builds the same paths from $HOME (hooks/register.tsx, paths()): each name the Python side uses is there.
mod = (ROOT / "hooks" / "register.tsx").read_text()
for p in [home.SOCKET, home.LOG, new_session.FINISHED_DIR, new_session.WAITING_DIR]:
    ok(f"${{d}}/{p.relative_to(home.HOME)}" in mod, f"the mod uses the same {p.relative_to(home.HOME)}")
ok("${d}/run/owner" in mod and "${d}/run/restart`" in mod, "the mod reads the owner and restart files from run/")

code = subprocess.run(["git", "ls-files", "*.py", "*.tsx", "*.swift"], cwd=ROOT, capture_output=True, text=True).stdout.split()
in_tmp = [f for f in code if f != "tests/test_private_paths.py" and re.search(r"['\"]/tmp/", (ROOT / f).read_text())]
ok(not in_tmp, f"no code names a path in /tmp{': ' + ', '.join(in_tmp) if in_tmp else ''}")

print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
