"""Which Code-tab session you're in: the Claude desktop app's own record of it.

The app keeps one JSON file per session, with its title and `lastFocusedAt` (updated as you
switch). The session focused last is the one voice talks to.
"""

import asyncio
import json
from pathlib import Path

from loguru import logger

SESSIONS = Path.home() / "Library/Application Support/Claude/claude-code-sessions"


class FocusWatcher:
    def __init__(self, on_focus, root: Path = SESSIONS, every: float = 0.5):
        self.on_focus = on_focus  # (session_id, title, project): the CLI session id, as $.session.id() gives it
        self.root = root
        self.every = every
        self._seen: dict[Path, tuple[float, int, str, str, str]] = {}  # path -> (mtime, focusedAt, session, title, project)
        self.session: str | None = None

    def scan(self) -> tuple[str, str, str] | None:
        """The session focused last, its title, and its project (the folder it runs in)."""
        best = None
        for path in self.root.glob("*/*/local_*.json"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            cached = self._seen.get(path)
            if not cached or cached[0] != mtime:
                try:
                    d = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue  # mid-write: next scan
                project = Path(d.get("originCwd") or d.get("cwd") or "").name
                cached = (mtime, d.get("lastFocusedAt") or 0, d.get("cliSessionId") or "", d.get("title") or "", project)
                self._seen[path] = cached
            if cached[2] and (best is None or cached[1] > best[1]):
                best = cached
        return (best[2], best[3], best[4]) if best else None

    def poll(self):
        found = self.scan()
        if found and found[0] != self.session:
            self.session = found[0]
            logger.info(f"focused session: {found[0][:8]} ({found[1] or found[2]})")
            self.on_focus(*found)

    async def run(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # never let a bad file stop the watch
                logger.warning(f"focus watch: {e}")
            await asyncio.sleep(self.every)
