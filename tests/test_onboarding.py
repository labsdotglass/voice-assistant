"""The onboarding window's line protocol (no model, no Swift): onboarding.py reads the window's stdout as a
stream and tells the mod what happened; a stub program stands in for bin/onboarding."""

import _root  # noqa: F401  (the repo root on the import path)
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import first_run
import onboarding

results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def run_with(tmp: Path, lines: list[str], argv=()) -> list[str]:
    """onboarding.main() with a stub window that prints `lines` (and records its argv): -> what main printed."""
    stub = tmp / "stub"
    stub.write_text("#!/bin/sh\necho \"$@\" > " + str(tmp / "argv") + "\n" + "".join(f"echo '{l}'\n" for l in lines))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    os.environ["VOICE_ONBOARDING_PROGRAM"] = str(stub)
    onboarding.HOME = first_run.HOME = tmp
    onboarding.SEEN = tmp / "onboarded"
    first_run.STATE = tmp / "first-run.json"
    first_run.REQUEST = tmp / "first-run-request"
    onboarding.TOUR_OPEN = tmp / "tour-open"  # every HOME-derived path: no test reaches the real ~/.voice-assistant
    out = io.StringIO()
    old = sys.argv
    sys.argv = ["onboarding.py", *argv]
    try:
        with contextlib.redirect_stdout(out):
            onboarding.main()
    finally:
        sys.argv = old
    return out.getvalue().splitlines()


def saved_language(tmp: Path):
    try:
        return json.loads((tmp / "settings.json").read_text()).get("language")
    except OSError:
        return None


def test_language_line_starts_voice(tmp: Path):
    out = run_with(tmp, ["language he"])
    ok(out == ["start", "closed"], f"language he -> start, then closed when the stub exits ({out})")
    ok(saved_language(tmp) == "he", "the language is saved")
    ok(json.loads(first_run.STATE.read_text())["phase"] == "installing", "state is installing")
    ok((tmp / "onboarded").exists(), "seen marker is set")


def test_later_before_language(tmp: Path):
    out = run_with(tmp, ["later"])
    ok(out == ["later"], f"later -> later ({out})")
    ok(saved_language(tmp) is None and not first_run.STATE.exists(), "nothing saved, no state")


def test_close_after_language_keeps_voice(tmp: Path):
    out = run_with(tmp, ["language en", "closed"])
    ok(out == ["start", "closed"], f"start then closed, never later ({out})")


def test_hidden_shown_pass_through(tmp: Path):
    out = run_with(tmp, ["language en", "hidden", "shown", "done"])
    ok(out == ["start", "hidden", "shown", "done"], f"hidden/shown/done pass through ({out})")


def test_window_exit_without_lines_is_later(tmp: Path):
    out = run_with(tmp, [])
    ok(out == ["later"], f"a window that ends silently -> later ({out})")


def test_argv(tmp: Path):
    run_with(tmp, [], ["--replay", "--enroll"])
    argv = (tmp / "argv").read_text().split(" ", 1)[1]
    ok("--replay" in argv and "--enroll" in argv and "--languages" in argv, "replay, enroll and languages reach the window")
    langs = json.loads(argv.split("--languages ", 1)[1])
    ok([l["code"] for l in langs] == [l["code"] for l in onboarding.languages()], "languages JSON is the menu's list")


def test_saved_language_is_current(tmp: Path):
    # Shown again (/voice-tour, /voice-enroll), the window speaks the language voice is set to: it's marked current.
    (tmp / "settings.json").write_text(json.dumps({"language": "he"}))
    run_with(tmp, [], ["--replay"])
    langs = json.loads((tmp / "argv").read_text().split("--languages ", 1)[1])
    ok([l["code"] for l in langs if l.get("current")] == ["he"], "the saved language is marked current")


def test_take_request_claims_first(tmp: Path):
    first_run.REQUEST = tmp / "first-run-request"
    first_run.REQUEST.write_text("enroll\n")
    ok(first_run.take_request() == "enroll" and not list(tmp.glob("first-run-request*")), "request taken, no claim file left")


def test_first_run_is_single(tmp: Path):
    # A window is already open (tour-open holds a live pid): a second launch joins it instead of opening another.
    (tmp / "tour-open").write_text(str(os.getpid()))
    onboarding.TOUR_OPEN = tmp / "tour-open"
    out = run_with(tmp, ["language en"])
    ok(out == ["joined"], f"a live tour-open -> joined, no window ({out})")
    ok(not (tmp / "argv").exists(), "the second window never opened")
    ok(saved_language(tmp) is None and not first_run.STATE.exists(), "nothing saved")
    (tmp / "tour-open").write_text("999999999")  # a pid nobody has: the last window died without cleaning up
    out = run_with(tmp, ["language en"])
    ok(out == ["start", "closed"], f"a stale tour-open is ignored ({out})")


def test_claims_tour_open_before_build(tmp: Path):
    seen = []
    real = onboarding.build
    onboarding.TOUR_OPEN = tmp / "tour-open"
    onboarding.build = lambda: seen.append(onboarding.TOUR_OPEN.read_text() if onboarding.TOUR_OPEN.exists() else None)
    try:
        run_with(tmp, [])
    finally:
        onboarding.build = real
    ok(seen == [str(os.getpid())], f"tour-open holds this pid while the build runs ({seen})")
    ok(not onboarding.TOUR_OPEN.exists(), "the claim is removed when the window is gone")


def test_new_first_run_clears_leftovers(tmp: Path):
    first_run.STATE, first_run.REQUEST = tmp / "first-run.json", tmp / "first-run-request"
    for flags in ([], ["--replay"], ["--enroll"]):
        first_run.STATE.write_text('{"phase": "installing"}')
        first_run.REQUEST.write_text("later\n")
        run_with(tmp, [], flags)  # run_with points STATE at tmp again; the files are the same names
        gone = not first_run.STATE.exists() and not first_run.REQUEST.exists()
        ok(gone == (flags == []), f"leftover state/request {'cleared' if flags == [] else 'kept'} for {flags or 'a new first run'}")


def test_joined_while_language_chosen(tmp: Path):
    onboarding.TOUR_OPEN = tmp / "tour-open"
    onboarding.TOUR_OPEN.write_text(str(os.getpid()))
    first_run.STATE = tmp / "first-run.json"
    first_run.STATE.write_text('{"phase": "installing"}')
    out = run_with(tmp, [])
    ok(out == ["joined"] and first_run.STATE.exists(), f"joining leaves the chosen-language state alone ({out})")


def test_not_now_is_not_seen(tmp: Path):
    out = run_with(tmp, ["later"])
    ok(out == ["later"] and not (tmp / "onboarded").exists(), f"Not now before a language: not marked seen, Welcome comes again ({out})")
    run_with(tmp, [], ["--replay"])
    ok((tmp / "onboarded").exists(), "shown again (--replay): marked seen")


def test_install_failed_and_retry(tmp: Path):
    run_with(tmp, ["language en"])  # points every path at tmp; state installing
    out = run_with(tmp, ["language fr"], ["--install-failed", "No", "network"])
    state = json.loads(first_run.STATE.read_text())
    ok(out == [] and state == {"phase": "failed", "reason": "No network", "kind": "install"} and saved_language(tmp) == "en",
       f"--install-failed writes Couldn't install, opens no window ({out}, {state})")
    run_with(tmp, [], ["--install-retry"])
    ok(json.loads(first_run.STATE.read_text()) == {"phase": "installing"}, "--install-retry: back to installing")
    first_run.write("failed", reason="Network is unreachable")
    run_with(tmp, [], ["--install-retry"])
    ok(json.loads(first_run.STATE.read_text())["phase"] == "failed", "--install-retry leaves a download failure (its Retry is the window's)")


for t in (test_not_now_is_not_seen, test_install_failed_and_retry, test_new_first_run_clears_leftovers, test_joined_while_language_chosen, test_claims_tour_open_before_build, test_first_run_is_single, test_language_line_starts_voice, test_later_before_language, test_close_after_language_keeps_voice,
          test_hidden_shown_pass_through, test_window_exit_without_lines_is_later, test_argv, test_saved_language_is_current, test_take_request_claims_first):
    with tempfile.TemporaryDirectory() as d:
        t(Path(d))
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
