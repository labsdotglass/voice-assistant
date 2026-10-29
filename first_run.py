"""The first run's state file and setup progress.

The daemon writes the phase to a JSON file; the onboarding window polls it and answers through a
request file. Setup progress is measured from the bytes on disk, so it needs nothing from the
downloaders. Standard library only (huggingface_hub is imported inside targets_for): onboarding.py
imports this before anything else is set up.

The daemon's side: watch_setup reports setup (setup, ready, failed), serve_requests is the one reader of
the window's requests, and hold_until_done keeps the greeting until the first run is over. orb_hidden says when
the floating orb stays out of sight, the window showing its own.
"""

import json
import os
import threading
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Callable

from home import HOME

ROOT = Path(__file__).parent
STATE = HOME / "first-run.json"
REQUEST = HOME / "first-run-request"  # the window writes one of: enroll, later, skip, done, retry
KOKORO_CACHE_DIR = Path.home() / ".cache" / "pipecat" / "kokoro-onnx"  # where pipecat keeps Kokoro's ONNX files (setup.py)
# Where Kokoro's ONNX files come from: pipecat's own URLs (KOKORO_MODEL_URL, KOKORO_VOICES_URL), fetched here instead
# so a cut-off download never sits at the destination (pipecat writes straight to it and only checks it exists).
SHARED_URLS = {
    "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}
WINDOW = 10  # seconds of recent progress the ETA is measured over
EVERY = 0.5  # seconds between setup's progress writes
AGAIN_GRACE = 10  # seconds the mod has to open the window at Your voice before learning your voice again counts as windowless
SETTING_UP = ("installing", "setup", "failed")  # phases before Ready: a window hidden meanwhile comes back on Ready (or Setup stopped)


def write(phase: str, **fields) -> None:
    """Replaces the state file whole (tmp + rename), so the window never reads half of it."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(f"{STATE.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"phase": phase, **fields}))
    tmp.replace(STATE)


def take_request() -> str | None:
    """The window's pending request, removed so it is answered once: -> its text, or None.
    Claimed by renaming first, so a request written while this reads is kept for the next call."""
    claim = REQUEST.with_name(f"{REQUEST.name}.{os.getpid()}.claim")
    try:
        REQUEST.replace(claim)
    except FileNotFoundError:
        return None
    try:
        return claim.read_text().strip() or None
    finally:
        claim.unlink(missing_ok=True)


def files_of(path: Path) -> set[Path]:
    """The real files under a file or directory, by resolved path. huggingface_hub 2.2.0 moves each finished Xet
    download to the cache-wide store (<HF_HUB_CACHE>/blobs/<2 hex>/<hash>) and leaves a symlink in the repo's blobs/,
    so links are followed; resolving dedupes snapshots/ -> blobs/ -> store, and a file two repos share."""
    try:
        if not path.is_dir():
            return {path.resolve(strict=True)} if path.is_file() else set()
    except OSError:  # removed or renamed by the downloader between the check and the resolve
        return set()
    found = set()
    for root, _, names in os.walk(path):  # an in-progress <etag>.<8 hex>.incomplete sits in the repo's blobs/ itself
        for name in names:
            try:
                real = Path(root, name).resolve(strict=True)
                if real.is_file():
                    found.add(real)
            except OSError:  # renamed or removed by the downloader meanwhile, or a link to nothing yet
                pass
    return found


def total_size(files: set[Path]) -> int:
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:  # renamed or removed by the downloader meanwhile
            pass
    return total


def size_of(path: Path) -> int:
    """Bytes under a file or directory, each real file once (see files_of)."""
    return total_size(files_of(path))


def part(dest: Path) -> Path:
    """Where fetch writes dest until it's whole."""
    return dest.with_name(dest.name + ".part")


def fetch(url: str, dest: Path, size: int | None = None) -> None:
    """Downloads url to dest whole or not at all (via part(dest), then a rename). A dest of the wrong size (cut off
    by an earlier downloader) is fetched again."""
    if dest.exists() and (size is None or dest.stat().st_size == size):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = part(dest)
    with urllib.request.urlopen(url, timeout=60) as response, open(tmp, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    if size is not None and tmp.stat().st_size != size:
        got = tmp.stat().st_size
        tmp.unlink()
        raise OSError(f"{dest.name} came down incomplete ({got} of {size} bytes)")
    tmp.replace(dest)


def shared_files() -> dict[str, tuple[str, int]]:
    """Kokoro's ONNX files, every language's: name -> (url, bytes)."""
    sizes = json.loads((ROOT / "languages.json").read_text())["shared_files"]
    return {name: (SHARED_URLS[name], size) for name, size in sizes.items()}


def fetch_shared() -> None:
    for name, (url, size) in shared_files().items():
        fetch(url, KOKORO_CACHE_DIR / name, size)


class Progress:
    """How much of the setup downloads is on disk: targets are (file or directory, expected bytes)."""

    def __init__(self, targets: list[tuple[Path, int]], clock=time.monotonic):
        self.targets = targets
        self.clock = clock
        self.samples = [(clock(), self.done_bytes())]

    def done_bytes(self) -> int:
        # A file still being fetched counts by its part file, so the bar moves while it comes down. One union over
        # every target, so a store file two repos link to counts once.
        files: set[Path] = set()
        for path, _ in self.targets:
            files |= files_of(path) or files_of(part(path))
        return total_size(files)

    def total_bytes(self) -> int:
        return sum(expected for _, expected in self.targets)

    def eta_seconds(self) -> int | None:
        """Seconds left at the rate of the last WINDOW seconds; None until bytes have been seen arriving."""
        now, done = self.clock(), self.done_bytes()
        self.samples = [s for s in self.samples if s[0] >= now - WINDOW] + [(now, done)]
        then, before = self.samples[0]
        if now <= then or done <= before:
            return None
        return round(max(self.total_bytes() - done, 0) / ((done - before) / (now - then)))


def targets_for(language: str) -> list[tuple[Path, int]]:
    """What setup downloads for a language: its HF cache directories and Kokoro's ONNX files, with sizes from languages.json."""
    from huggingface_hub.constants import HF_HUB_CACHE

    data = json.loads((ROOT / "languages.json").read_text())
    entry = next(lang for lang in data["languages"] if lang["code"] == language)
    repos = [(Path(HF_HUB_CACHE) / ("models--" + repo.replace("/", "--")), size) for repo, size in entry["repos"].items()]
    return repos + [(KOKORO_CACHE_DIR / name, size) for name, size in data["shared_files"].items()]


def phase() -> str | None:
    try:
        return json.loads(STATE.read_text()).get("phase")
    except (OSError, ValueError, AttributeError):
        return None


def unfinished() -> bool:
    """A first run is underway: a daemon restarted meanwhile (the mod passes no VOICE_FIRST_TOUR then) carries it on.
    Learning your voice again (/voice-enroll, More > Learn my voice again) writes its states with again: not one."""
    try:
        state = json.loads(STATE.read_text())
        return state.get("phase") not in (None, "done") and not state.get("again")
    except (OSError, ValueError, AttributeError):
        return False


_handlers: dict[str, Callable[[], None]] = {}
_serving = threading.Lock()  # held by the one poller, once started


def on_request(value: str, handler: Callable[[], None]) -> None:
    """What to do when the window sends this request (a later registration replaces an earlier one)."""
    _handlers[value] = handler


def serve_requests(poll: float = 0.2) -> None:
    """Starts the one reader of the window's requests (take_request removes each, so a second would steal them).
    Each goes to its on_request handler; one nobody registered is dropped. Starting it again does nothing."""
    if not _serving.acquire(blocking=False):
        return

    def serve():
        while True:
            try:
                value = take_request()
                if value in _handlers:
                    _handlers[value]()
            except Exception:  # one failed request (or an unreadable file) doesn't end the window's others
                traceback.print_exc()
            time.sleep(poll)

    threading.Thread(target=serve, name="first-run requests", daemon=True).start()


_setup_running = threading.Lock()


def watch_setup(loader: Callable[[], None], language: str) -> None:
    """Runs loader (downloads and loads the speech models) in a thread, writing setup's progress every EVERY
    seconds, then ready, or failed with the reason; the window's retry runs it again after a failure."""
    targets = targets_for(language)

    def run():
        if not _setup_running.acquire(blocking=False):
            return  # already setting up: a retry meanwhile is a double click
        try:
            progress, finished, error = Progress(targets), threading.Event(), []

            def load():
                try:
                    loader()
                except Exception as e:
                    error.append(e)
                finally:
                    finished.set()

            threading.Thread(target=load, name="first-run setup", daemon=True).start()
            while True:
                try:
                    total = progress.total_bytes()
                    done = min(progress.done_bytes(), total)  # a cache can hold a little more than the files counted
                    downloading = done < total
                    eta = progress.eta_seconds() if downloading else None
                    write("setup", done=done, total=total, eta=eta, step="models" if downloading else "starting")
                except Exception:  # a missed progress write: the next one comes in EVERY seconds, and ready still follows
                    traceback.print_exc()
                if finished.wait(EVERY):
                    break
        finally:
            _setup_running.release()  # before failed is written, so the Retry it shows always gets a run
        if error:
            write("failed", reason=str(error[0]) or type(error[0]).__name__)
        else:
            write("ready")

    start = lambda: threading.Thread(target=run, name="first-run progress", daemon=True).start()
    on_request("retry", lambda: phase() == "failed" and start())  # only Couldn't download has a Retry
    start()


def hold_until_done(release: Callable[[], None], window_open: Callable[[], bool], poll: float = 0.5) -> Callable[[], None]:
    """The first run's end: the window's Start talking (the done request), or the window gone once setup is over
    (it never comes back then). Either way release() runs once, then the phase is done. -> that end, for Start
    talking said out loud when the window can't answer."""
    over, lock = threading.Event(), threading.Lock()

    def finish():
        with lock:
            if over.is_set():
                return
            over.set()
        release()
        write("done")

    def watch():
        while not over.wait(poll):
            now = phase()
            if now is not None and now not in SETTING_UP and now != "done" and not window_open():
                finish()

    on_request("done", finish)
    threading.Thread(target=watch, name="first-run end", daemon=True).start()
    return finish


def orb_hidden(first_tour: bool, phase: str | None, again_window: bool) -> bool:
    """Whether the floating orb stays hidden: the onboarding window has its own orb, so two would show. Through a
    first run until it's over (phase done: Start talking, or the window gone after setup, as hold_until_done
    releases the greeting), and while learning your voice again has the window open. Otherwise it shows."""
    return (first_tour and phase != "done") or again_window


class AgainWindow:
    """Learning your voice again (/voice-enroll, More > Learn my voice again) in the window at Your voice: open from
    the request until that window closes. A window that hasn't opened within grace seconds never will."""

    def __init__(self, window_open: Callable[[], bool], clock=time.monotonic, grace: float = AGAIN_GRACE):
        self.window_open, self.clock, self.grace = window_open, clock, grace
        self.since: float | None = None  # when it was asked for; None: not learning again in a window
        self.seen = False  # the window has been seen open since

    def opening(self) -> None:
        self.since, self.seen = self.clock(), False

    def showing(self) -> bool:
        if self.since is None:
            return False
        if self.window_open():
            self.seen = True
            return True
        if not self.seen and self.clock() - self.since < self.grace:
            return True  # the mod is still opening it
        self.since = None
        return False
