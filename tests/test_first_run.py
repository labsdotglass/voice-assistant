"""First run (no model): the state file is written whole, a request is taken once, and setup progress is
the bytes on disk, with an ETA from the recent rate; setup reports its progress, readiness or failure, a
retry reruns it, and the greeting waits for Start talking or for the window to be gone."""

import _root  # noqa: F401  (the repo root on the import path)
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import first_run

targets_for = first_run.targets_for  # the real one, for test_targets_for_language

results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def point_at(tmp: Path):
    first_run.STATE = tmp / "first-run.json"
    first_run.REQUEST = tmp / "first-run-request"


def test_write_is_atomic(tmp: Path):
    point_at(tmp)
    stop, seen = threading.Event(), []

    def reader():
        while not stop.is_set():
            try:
                seen.append(json.loads(first_run.STATE.read_text()))
            except FileNotFoundError:
                pass
            except ValueError:
                seen.append(None)  # a half-written file

    t = threading.Thread(target=reader)
    t.start()
    for i in range(300):
        first_run.write("setup", done=i, pad="x" * 50000)
    stop.set()
    t.join()
    ok(seen and None not in seen, f"a reader never sees partial JSON ({len(seen)} reads)")
    ok(json.loads(first_run.STATE.read_text())["phase"] == "setup" and not list(tmp.glob("*.tmp")), "phase is stored; no temp file left")


def test_take_request_once(tmp: Path):
    point_at(tmp)
    ok(first_run.take_request() is None, "no request file -> None")
    first_run.REQUEST.write_text("enroll\n")
    ok(first_run.take_request() == "enroll", "the request is returned")
    ok(first_run.take_request() is None and not first_run.REQUEST.exists(), "second call -> None, file removed")


def test_progress_counts_disk_bytes(tmp: Path):
    (tmp / "blobs").mkdir()
    (tmp / "blobs" / "a").write_bytes(b"1" * 100)
    (tmp / "blobs" / "b").write_bytes(b"2" * 250)
    (tmp / "blobs" / "x.incomplete").write_bytes(b"3" * 40)
    (tmp / "snapshots").mkdir()
    (tmp / "snapshots" / "link").symlink_to(tmp / "blobs" / "a")  # a symlink isn't a second copy
    single = tmp / "single.bin"
    single.write_bytes(b"4" * 10)
    p = first_run.Progress([(tmp / "blobs", 1000), (single, 50), (tmp / "missing", 5)])
    ok(p.done_bytes() == 100 + 250 + 40 + 10, f"done_bytes is the sum on disk, .incomplete included ({p.done_bytes()})")
    ok(p.total_bytes() == 1055, "total_bytes is the expected sum")


def shared_store_repo(cache: Path, repo: str, etag: str, xet_hash: str, size: int) -> Path:
    """A repo as huggingface_hub 2.2.0 leaves a finished Xet download (seen in a real download into an empty HF_HOME):
    <cache>/blobs/<2 hex>/<hash> is the file, the repo's blobs/<etag> links to it, its snapshot links to blobs/<etag>."""
    store = cache / "blobs" / xet_hash[:2]
    store.mkdir(parents=True, exist_ok=True)
    (store / xet_hash).write_bytes(b"\0" * size)
    (store / (xet_hash + ".refs")).write_text(f"{repo}/blobs/{etag}\n")  # the store's own bookkeeping, not a download
    blobs, snap = cache / repo / "blobs", cache / repo / "snapshots" / "rev"
    blobs.mkdir(parents=True)
    snap.mkdir(parents=True)
    (blobs / etag).symlink_to(Path("..", "..", "blobs", xet_hash[:2], xet_hash))
    (snap / "weights.safetensors").symlink_to(Path("..", "..", "blobs", etag))
    return cache / repo


def test_progress_follows_shared_store(tmp: Path):
    a = shared_store_repo(tmp, "models--org--a", "e1", "63" + "a" * 62, 300)
    ok(first_run.Progress([(a, 300)]).done_bytes() == 300, f"a repo whose blobs link into the shared store counts the store's file ({first_run.Progress([(a, 300)]).done_bytes()})")
    b = shared_store_repo(tmp, "models--org--b", "e2", "63" + "a" * 62, 300)  # the same file, linked from a second repo
    p = first_run.Progress([(a, 300), (b, 300)])
    ok(p.done_bytes() == 300, f"a shared file linked from two repos counts once ({p.done_bytes()})")
    # While downloading, 2.2.0 writes <etag>.<8 hex>.incomplete in the repo's own blobs/, then moves it to the store.
    (b / "blobs" / "e3.c295ca9f.incomplete").write_bytes(b"\0" * 70)
    ok(p.done_bytes() == 370, f"a download under way next to shared links counts ({p.done_bytes()})")


def test_eta(tmp: Path):
    f = tmp / "model"
    f.write_bytes(b"")
    now = [0.0]
    p = first_run.Progress([(f, 500_000_000)], clock=lambda: now[0])
    ok(p.eta_seconds() is None, "no ETA before there is a rate")
    now[0] = 10.0
    f.write_bytes(b"\0" * 100_000_000)
    ok(p.eta_seconds() == 40, f"100 MB in 10 s, 400 MB left -> 40 s ({p.eta_seconds()})")


def test_targets_for_language():
    he = {str(p.name) for p, _ in first_run.targets_for("he")}
    en = {str(p.name) for p, _ in first_run.targets_for("en")}
    ok("models--el-gast--ivrit-v3-turbo-q8-mlx" in he and "models--mlx-community--whisper-large-v3-mlx" not in he, "he: ivrit Whisper, not whisper-large-v3")
    ok("models--mlx-community--whisper-large-v3-mlx" in en and "models--el-gast--ivrit-v3-turbo-q8-mlx" not in en, "en: whisper-large-v3, not ivrit")
    ok({"kokoro-v1.0.onnx", "voices-v1.0.bin"} <= he, "Kokoro's ONNX files are included")


def states_until(phase: str, timeout: float = 10.0) -> list[dict]:
    """Every distinct state the window would see, polling as it does, until one with this phase."""
    seen, end = [], time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            state = json.loads(first_run.STATE.read_text())
        except (OSError, ValueError):
            state = None
        if state and (not seen or state != seen[-1]):
            seen.append(state)
            if state["phase"] == phase:
                break
        time.sleep(0.05)
    return seen


def test_setup_writes_progress(tmp: Path):
    point_at(tmp)
    model = tmp / "model.bin"
    first_run.targets_for = lambda language: [(model, 1000)]

    def loader():  # a download arriving in pieces
        for i in range(1, 6):
            model.write_bytes(b"\0" * 200 * i)
            time.sleep(0.4)

    first_run.watch_setup(loader, "en")
    seen = states_until("ready")
    setup = [s for s in seen if s["phase"] == "setup"]
    dones = [s["done"] for s in setup]
    ok(len(setup) >= 2 and dones == sorted(dones) and dones[-1] > dones[0], f"at least two setup states, done rising ({dones})")
    ok(all(s["total"] == 1000 and "eta" in s and s["step"] in ("models", "starting") for s in setup), "each has total, eta and step")
    ok(all(s["done"] <= s["total"] for s in setup), "done never shows more than the total")
    ok(seen and seen[-1] == {"phase": "ready"}, f"then ready ({seen[-1] if seen else None})")


def test_setup_reports_failure(tmp: Path):
    point_at(tmp)
    first_run.targets_for = lambda language: [(tmp / "model.bin", 1000)]
    calls = []

    def loader():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("Network is unreachable")
        time.sleep(0.6)

    first_run.serve_requests()
    first_run.watch_setup(loader, "en")
    seen = states_until("failed")
    ok(seen and seen[-1] == {"phase": "failed", "reason": "Network is unreachable"}, f"failed, with the reason ({seen[-1] if seen else None})")
    first_run.REQUEST.write_text("retry")
    again = states_until("ready")
    ok(any(s["phase"] == "setup" for s in again) and again[-1]["phase"] == "ready", f"retry: setup again, then ready ({[s['phase'] for s in again]})")
    ok(len(calls) == 2, f"the loader ran again ({len(calls)} runs)")


def test_requests_dispatch(tmp: Path):
    point_at(tmp)
    got = []
    first_run.on_request("enroll", lambda: got.append("enroll"))
    first_run.serve_requests()
    first_run.serve_requests()  # started twice: still one poller
    ok(sum(t.name == "first-run requests" for t in threading.enumerate()) == 1, "one poller takes the requests")
    for value in ("nonsense", "enroll"):
        first_run.REQUEST.write_text(value)
        end = time.monotonic() + 3
        while first_run.REQUEST.exists() and time.monotonic() < end:
            time.sleep(0.05)
    time.sleep(0.3)
    ok(got == ["enroll"], f"each request reaches its handler once; an unknown one is ignored ({got})")


def test_size_of_vanished_file(tmp: Path):
    gone = tmp / "gone.bin"  # a file the downloader renamed between the check and the stat
    real = Path.is_file
    Path.is_file = lambda self: True if self == gone else real(self)
    try:
        ok(first_run.size_of(gone) == 0, "a file gone between the check and the stat counts 0")
    except OSError as e:
        ok(False, f"a file gone between the check and the stat counts 0 (raised {e!r})")
    finally:
        Path.is_file = real


def test_request_loop_survives(tmp: Path):
    point_at(tmp)  # the poller test_requests_dispatch started is still the one running
    got, real = [], first_run.take_request

    def broken():
        first_run.take_request = real
        raise OSError("unreadable request")

    first_run.on_request("later", lambda: got.append("later"))
    first_run.take_request = broken
    time.sleep(0.5)
    first_run.REQUEST.write_text("later")
    end = time.monotonic() + 3
    while not got and time.monotonic() < end:
        time.sleep(0.05)
    first_run.take_request = real
    ok(got == ["later"], f"a request that couldn't be read doesn't end the loop: the next one is answered ({got})")


def test_progress_loop_survives(tmp: Path):
    point_at(tmp)
    model = tmp / "model.bin"
    first_run.targets_for = lambda language: [(model, 1000)]
    real, failures = first_run.write, []

    def flaky(phase, **fields):
        if phase == "setup" and not failures:
            failures.append(1)
            raise OSError("disk full")
        real(phase, **fields)

    first_run.write = flaky
    try:
        first_run.watch_setup(lambda: (time.sleep(1.2), model.write_bytes(b"\0" * 1000)), "en")
        seen = states_until("ready")
    finally:
        first_run.write = real
    ok(failures and any(s["phase"] == "setup" for s in seen) and seen[-1] == {"phase": "ready"},
       f"a failed progress write doesn't end setup's reports: setup, then ready ({[s['phase'] for s in seen]})")


def test_greeting_held_until_done(tmp: Path):
    point_at(tmp)
    released = []
    first_run.write("ready")
    first_run.serve_requests()
    first_run.hold_until_done(lambda: released.append(1), window_open=lambda: True)
    time.sleep(1.2)
    ok(not released, "while the window is open, the greeting waits")
    first_run.REQUEST.write_text("done")
    seen = states_until("done", timeout=3)
    time.sleep(1.2)
    ok(released == [1] and seen[-1] == {"phase": "done"}, f"Start talking: released once, phase done ({released}, {seen[-1]})")


def test_greeting_released_when_window_gone(tmp: Path):
    point_at(tmp)
    released, window = [], {"open": True}
    first_run.write("setup", done=0, total=1, eta=None, step="models")
    first_run.hold_until_done(lambda: released.append(1), window_open=lambda: window["open"])
    window["open"] = False
    time.sleep(1.2)
    ok(not released, "the window gone during setup: it comes back on Ready, so the greeting still waits")
    first_run.write("ready")
    states_until("done", timeout=3)
    ok(released == [1] and json.loads(first_run.STATE.read_text())["phase"] == "done", "the window gone after Ready: released, phase done")


def test_shared_files_fetched_by_setup(tmp: Path):
    point_at(tmp)
    source, cache = tmp / "source", tmp / "cache"
    source.mkdir()
    first_run.KOKORO_CACHE_DIR = cache
    first_run.shared_files = lambda: {"model.onnx": ((source / "model.onnx").as_uri(), 5000), "voices.bin": ((source / "voices.bin").as_uri(), 300)}
    first_run.targets_for = lambda language: [(cache / "model.onnx", 5000), (cache / "voices.bin", 300)]
    cache.mkdir()
    first_run.part(cache / "model.onnx").write_bytes(b"\0" * 4000)  # a download cut off by a crash
    (cache / "voices.bin").write_bytes(b"\0" * 100)  # pipecat's own download, cut off at its destination
    p = first_run.Progress(first_run.targets_for("en"))
    ok(p.done_bytes() == 4100, f"a download under way counts on the bar ({p.done_bytes()})")
    (source / "voices.bin").write_bytes(b"v" * 300)  # the other source is missing: the network is down

    first_run.serve_requests()
    first_run.watch_setup(lambda: (time.sleep(0.3), first_run.fetch_shared()), "en")  # slow enough for the poll to see setup
    seen = states_until("failed")
    ok(bool(seen and seen[-1]["phase"] == "failed" and seen[-1]["reason"]), f"a shared file that can't be fetched: failed, with a reason ({seen[-1] if seen else None})")
    ok(not (cache / "model.onnx").exists(), "the partial file never stands in for the finished one")
    (source / "model.onnx").write_bytes(b"m" * 5000)  # the network is back
    first_run.REQUEST.write_text("retry")
    again = states_until("ready")
    ok(any(s["phase"] == "setup" for s in again) and again[-1]["phase"] == "ready", f"retry: setup, then ready ({[s['phase'] for s in again]})")
    ok((cache / "model.onnx").read_bytes() == b"m" * 5000 and (cache / "voices.bin").read_bytes() == b"v" * 300,
       "both files fetched whole: the cut-off one at its destination fetched again")
    ok(not first_run.part(cache / "model.onnx").exists(), "no partial file left")


def test_unfinished(tmp: Path):
    point_at(tmp)
    ok(not first_run.unfinished(), "no state file: no first run underway")
    first_run.write("setup", done=0, total=1, eta=None, step="models")
    ok(first_run.unfinished(), "phase setup: a first run is underway (a restarted daemon carries it on)")
    first_run.write("done")
    ok(not first_run.unfinished(), "phase done: over")


def test_orb_hidden(tmp: Path):
    """The floating orb hides while the onboarding window shows its own: through a first run, and learning your voice again."""
    hidden = first_run.orb_hidden
    for phase in (None, "installing", "setup", "failed", "ready"):
        ok(hidden(first_tour=True, phase=phase, again_window=False), f"first run, phase {phase}: the orb is hidden")
    ok(not hidden(first_tour=True, phase="done", again_window=False), "first run over (Start talking, or the window gone after setup): the orb shows")
    ok(not hidden(first_tour=False, phase=None, again_window=False), "no first run: the orb shows as always")
    ok(not hidden(first_tour=False, phase="setup", again_window=False), "a stale state file without a first run: the orb shows")
    ok(hidden(first_tour=False, phase="done", again_window=True), "learning your voice again, its window open: hidden")


def test_again_window(tmp: Path):
    """Learning your voice again: hidden from the request until its window closes; one that never opens lets go after a grace."""
    is_open, now = [False], [0.0]
    w = first_run.AgainWindow(window_open=lambda: is_open[0], clock=lambda: now[0], grace=10)
    ok(not w.showing(), "nothing asked: no window")
    w.opening()
    ok(w.showing(), "just asked, the mod still opening the window: counts as open")
    now[0] = 3; is_open[0] = True
    ok(w.showing(), "the window is up")
    now[0] = 60
    ok(w.showing(), "still up a minute later")
    is_open[0] = False
    ok(not w.showing(), "the window closed: over")
    now[0] = 61; is_open[0] = True
    ok(not w.showing(), "a later window (the tour) doesn't count")
    is_open[0] = False
    w.opening()
    now[0] = 70
    ok(w.showing(), "asked again, not open yet within the grace")
    now[0] = 72
    ok(not w.showing(), "never opened within the grace: over")


def test_helper_starts_hidden(tmp: Path):
    """A helper started while the orb is hidden gets --orb-hidden, so its window never flashes up."""
    import helper_transport
    h = helper_transport.AudioHelper(24000)
    if not h._has_orb:
        ok(True, "no orb page built: nothing to hide (skipped)")
        return
    ok("--orb-hidden" not in h.orb_args(), "the orb shown: no --orb-hidden")
    h.set_orb(hidden=True)
    ok(h.orb_args()[-1] == "--orb-hidden" and h._orb["hidden"], "set hidden before it starts: launched hidden, and the state says so")


def main():
    for test in (test_write_is_atomic, test_take_request_once, test_progress_counts_disk_bytes, test_progress_follows_shared_store, test_eta, test_setup_writes_progress,
                 test_setup_reports_failure, test_requests_dispatch, test_request_loop_survives, test_size_of_vanished_file, test_progress_loop_survives, test_greeting_held_until_done, test_greeting_released_when_window_gone,
                 test_shared_files_fetched_by_setup, test_unfinished, test_orb_hidden, test_again_window, test_helper_starts_hidden):
        with tempfile.TemporaryDirectory() as d:
            test(Path(d))
    first_run.targets_for = targets_for
    test_targets_for_language()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


main()
