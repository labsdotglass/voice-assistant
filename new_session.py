"""Starting a new Claude Code session by voice, and moving the desktop app to it.

The app won't start a session from a spoken request, so this goes through the terminal CLI, as you
would by hand: `claude -p "<message>"` in the folder starts the session and runs its first turn,
then `claude --desktop --resume <id>` opens it in the desktop app, which focuses it (and voice
follows). --desktop insists on a terminal, so it runs on a pseudo-terminal here.
"""

import asyncio
import glob
import json
import os
import pty
import re
import select
import shutil
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

import auto_mode
from home import RUN



def projects_folder() -> Path:
    """Where your projects live, for "a new session in <project>": VOICE_PROJECTS if set, else the first of the
    usual folders that exists, else your home folder."""
    if os.environ.get("VOICE_PROJECTS"):
        return Path(os.environ["VOICE_PROJECTS"]).expanduser()
    home = Path.home()
    return next((home / d for d in ("dev", "Developer", "code", "projects", "src") if (home / d).is_dir()), home)


DEV = projects_folder()


def find_claude() -> str | None:
    """The Claude Code CLI: on PATH, where its installers put it, or the desktop app's own copy."""
    found = shutil.which("claude")
    if found:
        return found
    home = str(Path.home())
    for pattern in (f"{home}/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude",
                    f"{home}/.nvm/versions/node/*/bin/claude", f"{home}/.claude/local/claude",
                    f"{home}/Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def resolve_folder(spoken: str) -> Path | None:
    """Where the session runs, from what you said: a project in your projects folder by name, that folder when
    you named none, or None when no project has that name (voice asks, rather than guessing a folder)."""
    name = spoken.strip().strip("/").lower().replace(" ", "-")
    if name in ("", "dev", "~/dev", DEV.name.lower()):
        return DEV
    for folder in DEV.iterdir() if DEV.is_dir() else []:
        if folder.is_dir() and folder.name.lower() in (name, name.replace("-", "")):
            return folder
    return None


def _on_a_terminal(argv: list[str], cwd: Path, wait: float = 30) -> str:
    """Runs argv on a pseudo-terminal (as if typed in one) until it exits; -> what it printed."""
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd)
            os.execv(argv[0], argv)
        finally:
            os._exit(127)  # exec failed: never carry on as a second copy of the daemon
    out, end = b"", time.time() + wait
    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], 0.3)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        if os.waitpid(pid, os.WNOHANG)[0]:
            break
    else:  # still running after the wait: stop it
        try:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        except OSError:
            pass
    os.close(fd)
    return out.decode(errors="replace").replace("\r", "")


async def start(message: str, folder: str = "", name: str = "") -> tuple[bool, str]:
    """Starts a session in the folder with your first message, then opens it in the app.
    -> (started?, what happened, for voice to say)."""
    claude = find_claude()
    if not claude:
        return False, "Claude Code's terminal command isn't installed, so voice can't start sessions."
    cwd = resolve_folder(folder)
    if cwd is None:
        return False, f"There's no project called {folder} in {DEV.name}, so no session was started."
    argv = [claude, "-p", "--output-format", "json"]
    # Its first turn runs in auto mode only if that's already your default (auto_mode.py): voice never turns it on.
    if auto_mode.mode() == "auto":
        argv += ["--permission-mode", "auto"]
    if name:
        argv += ["--name", name]
    argv += ["--", message or "Hi"]  # after --: a message that starts with "-" is still the prompt, not an option
    logger.info(f"[new session] starting in {cwd}: {message!r} (name {name!r})")
    proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 600)
    except asyncio.TimeoutError:
        proc.kill()
        return False, "The new session's first turn took over ten minutes, so it wasn't opened."
    try:
        session = json.loads(out)["session_id"]
    except (ValueError, KeyError, TypeError):
        logger.warning(f"[new session] start failed: {(err or out).decode(errors='replace')[-300:]}")
        return False, "The new session didn't start."
    said = await asyncio.to_thread(_on_a_terminal, [claude, "--desktop", "--resume", session], cwd)
    if "Opening session" not in said:
        logger.warning(f"[new session] hand-off failed: {said[-300:]}")
        return False, f"The session started but didn't open in the app (id {session})."
    logger.info(f"[new session] {session} open in the app ({cwd})")
    where = DEV.name if cwd == DEV else cwd.name
    return True, f"The new session{' ' + repr(name) if name else ''} is open in the app, in {where}."


async def on_the_orb(orb, opening, settle: float = 0) -> tuple[bool, str]:
    """Runs opening (start or go_to) with a spinner in the orb's middle, so the wait isn't silent.
    Once it's open, the spinner stays settle seconds more (while the app switches to it and voice
    starts afresh there), then goes with a tick, just before voice says so. Cleared at once if it
    fails or breaks, with no tick. -> what opening returned."""
    if orb:
        orb.set_orb(opening=True)
    ok = False
    try:
        ok, said = await opening
        if ok:
            await asyncio.sleep(settle)
    finally:
        if orb:
            orb.set_orb(opening=False)
    if ok and orb:
        orb.tick()  # you hear it's open, before voice says so
    return ok, said


# Going to a session you name: the app keeps one record per session (focus.py reads the same ones).
SESSIONS = Path.home() / "Library/Application Support/Claude/claude-code-sessions"
TRANSCRIPTS = Path.home() / ".claude" / "projects"  # every session's transcript: <project>/<session id>.jsonl


def last_said(session: str, root: Path = TRANSCRIPTS, most: int = 8_000_000, chunk: int = 500_000) -> dict:
    """When the user last wrote to a session and when it last replied, from its transcript (the mod's view of
    the conversation carries no times): {"user": epoch secs or None, "reply": ...}. Your words are a user
    entry with text of its own (not a tool's result, not only the app's notes); a reply, assistant text. Read
    from the end, a chunk at a time, `most` bytes at most: a long session's file runs to megabytes."""
    found = {"user": None, "reply": None}
    files = list(root.glob(f"*/{session}.jsonl")) if session else []
    if not files:
        return found
    try:
        with files[0].open("rb") as f:
            size = f.seek(0, 2)
            read = 0
            while read < min(size, most) and not (found["user"] and found["reply"]):
                read = min(size, read + chunk)
                f.seek(size - read)
                lines = f.read(read).decode(errors="replace").splitlines()
                for line in reversed(lines[1:] if read < size else lines):
                    _when_said(line, found)
                    if found["user"] and found["reply"]:
                        break
    except OSError:
        pass
    return found


def _when_said(line: str, found: dict):
    try:
        entry = json.loads(line)
    except ValueError:
        return
    key = {"user": "user", "assistant": "reply"}.get(entry.get("type"))
    if not key or found[key] or not entry.get("timestamp"):
        return
    content = (entry.get("message") or {}).get("content")
    texts = [content] if isinstance(content, str) else [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
    words = re.sub(r"<([a-z][\w-]*)[^>]*>[\s\S]*?</\1>", "", " ".join(t for t in texts if t)).strip()
    if words and not words.startswith("[Request interrupted"):
        try:
            found[key] = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass


def _words(text: str) -> set[str]:
    import re
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in {"the", "a", "an", "session", "in", "on", "of", "my"}}


def list_sessions(limit: int = 12, root: Path = SESSIONS) -> list[dict]:
    """Your open sessions in the app, the one you visited last first: {id, cwd, title, project, last}."""
    rows = []
    for path in root.glob("*/*/local_*.json"):
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if d.get("isArchived") or not d.get("cliSessionId") or not d.get("cwd"):
            continue
        rows.append({"id": d["cliSessionId"], "app_id": path.stem, "cwd": d["cwd"], "title": d.get("title") or "untitled",
                     "project": Path(d.get("originCwd") or d["cwd"]).name, "last": (d.get("lastFocusedAt") or 0) / 1000})
    rows.sort(key=lambda r: r["last"], reverse=True)
    return rows[:limit]


def _summary_of(d: dict) -> dict | None:
    """The app's summary of a session's last turn, once the app has written it: {status, detail, needs}.
    It's the last turn's only when it names the last reply; until then it's the turn before's."""
    s = d.get("postTurnSummary") or {}
    if not s or d.get("postTurnSummaryFor") != d.get("lastAssistantUuid"):
        return None
    return {"status": s.get("status_category", ""), "detail": s.get("status_detail", ""), "needs": s.get("needs_action", "")}


def turn_summary(session_id: str, root: Path = SESSIONS) -> dict | None:
    """A session's title and project, and the app's summary of its last turn (None until written):
    {title, project, summary}. None if the app has no such session."""
    for path in root.glob("*/*/local_*.json"):
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if d.get("cliSessionId") == session_id:
            return {"title": d.get("title") or "", "project": Path(d.get("originCwd") or d.get("cwd") or "").name,
                    "summary": _summary_of(d)}
    return None


def app_id(session_id: str, root: Path = SESSIONS) -> str:
    """The app's own id for a session ("local_…", what its archive tool takes), from the CLI session id; ""
    if the app has no such session. The two often differ: the app's isn't always the CLI's with a prefix."""
    for path in root.glob("*/*/local_*.json"):
        try:
            if json.loads(path.read_text()).get("cliSessionId") == session_id:
                return path.stem
        except (OSError, ValueError):
            continue
    return ""


# The note each session's mod leaves when it finishes a turn (hooks/register.tsx), voice on or off:
# {id, record, at, answer}. The band above the prompt shows them; voice tells you about them when it starts.
FINISHED_DIR = RUN / "finished"
NOTE_LIFE = 24 * 3600  # a note nobody acted on for a day is dropped


def _live_notes(folder: Path, me: str, now: float):
    """The notes in a folder, other than `me`'s, with their session's record: (note, record, at). A note is
    stale (and removed) once you've visited its session since, it's archived, or it's a day old."""
    for path in folder.glob("*.json") if folder.is_dir() else []:
        try:
            note = json.loads(path.read_text())
            d = json.loads(Path(note["record"]).read_text())
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if note.get("id") == me:
            continue
        at = note.get("at", 0) / 1000
        if d.get("isArchived") or now - at > NOTE_LIFE or (d.get("lastFocusedAt") or 0) / 1000 > at:
            path.unlink(missing_ok=True)
            continue
        yield note, d, at


def _project(d: dict) -> str:
    return Path(d.get("originCwd") or d.get("cwd") or "").name


def finished_elsewhere(me: str = "", folder: Path = FINISHED_DIR, now: float | None = None) -> list[dict]:
    """The sessions that finished a turn you haven't seen yet, other than `me`, newest first:
    {id, title, project, at, summary, answer}."""
    rows = [{"id": note["id"], "title": d.get("title") or "untitled", "at": at, "answer": note.get("answer", ""),
             "project": _project(d), "summary": _summary_of(d)}
            for note, d, at in _live_notes(folder, me, now or time.time())]
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows


# The note a session's mod leaves when it starts waiting for you (a permission, a question form) while voice
# is off: {id, record, at, what}, one file per session, removed once the wait is answered. When voice starts,
# it tells you which sessions are waiting.
WAITING_DIR = RUN / "waiting"


def waiting_elsewhere(me: str = "", folder: Path = WAITING_DIR, now: float | None = None) -> list[dict]:
    """The sessions other than `me` waiting for you, newest first: {id, title, project, at, what}."""
    rows = [{"id": note["id"], "title": d.get("title") or "untitled", "at": at, "what": note.get("what", ""),
             "project": _project(d)}
            for note, d, at in _live_notes(folder, me, now or time.time())]
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows


WAIT_WORDS = 160  # how much of a question goes into "an answer: ...", here and in the mod's note


def wait_described(ev: dict) -> str:
    """What a session's waiting event asks of you, in a few words."""
    if ev.get("type") == "permission":
        return f"your permission to {ev.get('what') or 'go on'}"
    asked = next((q.get("question", "") for q in ev.get("questions") or [] if q.get("question")), "")
    return f"an answer: {asked[:WAIT_WORDS]}" if asked else "an answer to its question"


class Waits:
    """Which other sessions' waits voice has told you about: each once, until that session moves on."""

    def __init__(self):
        self.told: dict[str, set[str]] = {}

    @staticmethod
    def _key(ev: dict) -> str:
        return f"{ev.get('type')}:{ev.get('what') or ev.get('id') or ''}"

    def new(self, ev: dict) -> bool:
        """True the first time this wait is seen (a permission by what it asks, a question by its id)."""
        told = self.told.setdefault(ev.get("session", ""), set())
        if self._key(ev) in told:
            return False
        told.add(self._key(ev))
        return True

    def still(self, ev: dict) -> bool:
        """Whether this wait is still open (its session hasn't moved on since it was seen)."""
        return self._key(ev) in self.told.get(ev.get("session", ""), set())

    def moved_on(self, session: str) -> bool:
        """The session answered or carried on (a new turn, a turn's end, a permission settled, a form answered).
        True if voice had told you about a wait there."""
        return bool(self.told.pop(session, None))


def when(seconds: float, now: float | None = None) -> str:
    """When you were last there, as you'd say it."""
    now = now or time.time()
    days = int((now - seconds) // 86400) if seconds else None
    if days is None:
        return "never visited"
    if time.localtime(seconds).tm_yday == time.localtime(now).tm_yday and days == 0:
        return "today"
    return "yesterday" if days <= 1 else f"{days} days ago"


def find_session(name: str, project: str = "", root: Path = SESSIONS) -> tuple[dict | None, list[str]]:
    """The session whose title best matches what you said, in the project you named if any.
    -> (the session {id, cwd, title}, or None; the closest titles when it's unclear or nothing fits)."""
    said, place = _words(name), _words(project)
    live = []
    for path in root.glob("*/*/local_*.json"):
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not d.get("isArchived") and d.get("cliSessionId") and d.get("cwd"):
            live.append((path, d))

    def folder_words(d: dict) -> set[str]:
        return _words(Path(d.get("originCwd") or d["cwd"]).name.replace("-", " ").replace("_", " "))

    # A project named, not a session: what you said is a project's name (or only a project was given). Then
    # it's that project's last active session, not one whose title happens to share the word.
    asked_project = said if said and any(folder_words(d) == said for _, d in live) else (place if not said else set())
    if asked_project:
        in_project = [(d.get("lastActivityAt") or d.get("lastFocusedAt") or 0, path, d) for path, d in live
                      if folder_words(d) == asked_project]
        if in_project:
            _, path, d = max(in_project, key=lambda r: r[0])
            return {"id": d["cliSessionId"], "app_id": path.stem, "cwd": d["cwd"], "title": d.get("title") or ""}, []

    scored = []
    for path, d in live:
        title = d.get("title") or ""
        folder = Path(d.get("originCwd") or d["cwd"]).name
        if place and not place <= _words(folder.replace("-", " ")):
            continue
        hits = said & _words(title)
        score = len(hits) / max(1, len(said))
        if score >= 0.5:
            scored.append((score, d.get("lastFocusedAt") or 0, {"id": d["cliSessionId"], "app_id": path.stem, "cwd": d["cwd"], "title": title}))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    if not scored:
        return None, []
    best = scored[0]
    rivals = [s for s in scored[1:] if s[0] == best[0]]
    if rivals:  # as good a match: ask which, newest first
        return None, [s[2]["title"] for s in [best] + rivals][:3]
    return best[2], []


async def go_to(name: str, project: str = "", session_id: str = "") -> tuple[bool, str]:
    """Opens the session you named (or the one with this id, from a list voice read you) in the app,
    as `claude --desktop --resume <id>` run from its folder. -> (opened?, what happened, for voice to say)."""
    by_id = [r for r in list_sessions(1000) if r["id"] == session_id] if session_id else []
    found, closest = (by_id[0], []) if by_id else find_session(name, project)
    if not found:
        if closest:
            return False, "More than one session fits: " + "; ".join(closest) + ". Ask which one."
        return False, f"No session called {name!r}" + (f" in {project}" if project else "") + " was found."
    logger.info(f"[go to session] {found['title']!r} ({found['id'][:8]}) in {found['cwd']}")
    # The app's own link to the session opens it whether it's running or not. (claude --desktop --resume
    # refuses a running one: "open in another terminal".) The terminal route stays as a fallback.
    link = f"claude://claude.ai/epitaxy/{found['app_id']}"
    opened = await asyncio.create_subprocess_exec("/usr/bin/open", link)
    if await opened.wait() == 0:
        return True, f"Opened {found['title']!r} ({Path(found['cwd']).name})."
    logger.warning(f"[go to session] its link didn't open ({link}); trying the terminal")
    claude = find_claude()
    if not claude:
        return False, f"The session {found['title']!r} didn't open."
    said = await asyncio.to_thread(_on_a_terminal, [claude, "--desktop", "--resume", found["id"]], Path(found["cwd"]))
    if "Opening session" not in said:
        logger.warning(f"[go to session] didn't open: {said[-300:]}")
        return False, f"The session {found['title']!r} didn't open."
    return True, f"Opened {found['title']!r} ({Path(found['cwd']).name})."
