"""Runs the checks: the offline ones (protocol, speech path; no model) and the ones that talk to the
talker's model (Haiku), all in parallel.

    uv run run_tests.py               # all of them
    uv run run_tests.py --offline     # only the fast ones that need no model (before each commit)
    uv run run_tests.py --no-models   # the offline ones that download nothing either (CI, .github/workflows)
    uv run run_tests.py --changed     # only the checks your uncommitted changes can affect
    uv run run_tests.py form confirm  # these by name (tests/test_form.py, tests/test_confirm.py)
    uv run run_tests.py --compare main voice_menu   # pass rates here vs on main, 6 runs a side (--runs N)

A check that talks to the model and fails is run once more: the model's wording varies from run to
run, so a pass on the retry is reported as flaky, not hidden. Exits non-zero if any check still fails.

After a change to the talker's prompt or a tool's description, one pass proves little: a wording change once
made the talker call a tool twice in 4 of 10 runs, against 0 of 9 on main. --compare runs the model checks
several times on this tree and on a ref (checked out in a scratch worktree) and prints the pass rates side
by side. It only reports, as those checks vary run to run.
"""

import ast
import os
import tempfile
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
TESTS = "tests"  # where the checks live; the lists below name them by file
OFFLINE = ["test_protocol.py", "test_private_paths.py", "test_settings.py", "test_interrupt.py", "test_speech.py", "test_speaker.py", "test_ending.py", "test_loading.py", "test_tasks.py", "test_draft.py", "test_early.py", "test_turn_end.py", "test_fast_voice.py", "test_hebrew_reading.py", "test_hebrew_terms.py", "test_restart_in_place.py", "test_finished.py", "test_waiting.py", "test_opening.py", "test_parity.py", "test_translations.py", "test_first_run.py", "test_quiet_first_run.py", "test_onboarding.py", "test_enroll_phrases.py", "test_release.py", "test_setup.py"]
TALKER = ["test_talker.py", "test_session_text.py", "test_naming.py", "test_cancel.py", "test_end_voice.py", "test_greeting.py", "test_progress.py", "test_concise.py", "test_pass_on.py", "test_opener.py", "test_ack_first.py", "test_decision.py", "test_form.py", "test_voice_menu.py", "test_menu_switches.py", "test_menu_sessions.py", "test_menu_tour.py", "test_first_run_listener.py", "test_switch.py", "test_confirm.py", "test_sessions.py", "test_brief.py", "test_finished_elsewhere.py", "test_rename.py", "test_archive.py", "test_full_answer.py", "test_judgment.py", "test_one_prompt.py", "test_waiting_elsewhere.py", "test_refused.py", "test_follow_up.py", "test_restart.py", "test_typed.py", "test_catch_up.py", "test_nikud.py"]  # each a few model calls
# Offline checks that still need the speech models on disk (gigabytes): left out by --no-models.
NEEDS_MODELS = {"test_speaker.py", "test_fast_voice.py", "test_hebrew_reading.py"}
MOD = "mod"  # the voice mod (hooks/, .claude-plugin/), checked by the engine itself: claude plugin validate
AT_ONCE = 6  # model checks running together: each holds its own conversations with the model
RUNS = 6  # --compare: runs of each check on each side


def located(check: str, root: Path = ROOT) -> str:
    """A check's path from the repo root: in tests/, or at the root in a tree from before the move (--compare)."""
    return f"{TESTS}/{check}" if (root / TESTS / check).exists() else check


def covers(check: str) -> set[str]:
    """The repo's own files a check runs (paths from the repo root): itself and every local module it imports,
    all the way down. A module is looked for beside the checks first, then at the root."""
    seen, todo = set(), [located(check)]
    while todo:
        name = todo.pop()
        if name in seen or not (ROOT / name).exists():
            continue
        seen.add(name)
        for node in ast.walk(ast.parse((ROOT / name).read_text())):
            if isinstance(node, ast.Import):
                todo += [located(a.name.split(".")[0] + ".py") for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                todo.append(located(node.module.split(".")[0] + ".py"))
    return seen


def changed() -> set[str]:
    """Files with uncommitted changes, staged or not, and new ones."""
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    return {line[3:].split(" -> ")[-1] for line in out.splitlines()}


def pick(args: list[str]) -> list[str]:
    names = [a for a in args if not a.startswith("--")]
    if names:
        return ([MOD] if MOD in names else []) + [c for c in OFFLINE + TALKER if c.removeprefix("test_").removesuffix(".py") in names or c in names]
    if "--offline" in args:
        return OFFLINE
    if "--no-models" in args:
        return [c for c in OFFLINE if c not in NEEDS_MODELS]
    if "--changed" in args:
        edits = changed()
        if any(e in ("run_tests.py",) for e in edits):
            return OFFLINE + TALKER
        mod = [MOD] if any(e.startswith(("hooks/", ".claude-plugin/", "types/")) for e in edits) else []
        return mod + [c for c in OFFLINE + TALKER if covers(c) & edits]
    return [MOD] + OFFLINE + TALKER


def run(check: str, root: Path = ROOT) -> tuple[str, int, str, float]:
    t0 = time.monotonic()
    if check == MOD:
        done = subprocess.run(["claude", "plugin", "validate", "."], cwd=root, capture_output=True, text=True)
        return check, done.returncode, done.stdout[-1500:] if done.returncode else "", time.monotonic() - t0
    # Checks keep out of your own voice history: the talker's record of its checks goes to a scratch file.
    scratch = Path(tempfile.mkdtemp())
    env = {**os.environ, "VOICE_CHECKS_FILE": str(scratch / "checks.json"), "VOICE_SETTINGS_FILE": str(scratch / "settings.json")}
    done = subprocess.run([sys.executable, located(check, root)], cwd=root, capture_output=True, text=True, env=env)
    # The check's own report, without Pipecat's and the SDK's log lines.
    report = "\n".join(l for l in done.stdout.splitlines() if not l.startswith("20"))
    return check, done.returncode, report, time.monotonic() - t0


def prompt_parts(source: str) -> list[str]:
    """What the talker's model is told: SYSTEM_PROMPT and each tool's name, description and fields."""
    parts = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SYSTEM_PROMPT" for t in node.targets):
            parts.append(ast.dump(node.value))
        elif isinstance(node, ast.AsyncFunctionDef):
            parts += [ast.dump(d) for d in node.decorator_list if isinstance(d, ast.Call) and getattr(d.func, "id", None) == "tool"]
    return parts


def prompt_changed(ref: str) -> bool:
    before = subprocess.run(["git", "show", f"{ref}:talker.py"], cwd=ROOT, capture_output=True, text=True)
    return before.returncode == 0 and prompt_parts(before.stdout) != prompt_parts((ROOT / "talker.py").read_text())


def compare(ref: str, args: list[str], runs: int):
    """Each model check `runs` times here and on `ref`, and the pass rates side by side. Only reports."""
    checks = [c for c in pick(args) if c in TALKER] if [a for a in args if not a.startswith("--")] else TALKER
    there = Path(tempfile.mkdtemp()) / "ref"
    if subprocess.run(["git", "worktree", "add", "--detach", str(there), ref], cwd=ROOT, capture_output=True).returncode:
        sys.exit(f"Can't check out {ref!r} to compare with.")
    print(f"{len(checks)} checks x {runs} runs, here and on {ref}: {2 * len(checks) * runs} runs, {AT_ONCE} at a time")
    try:
        jobs = [(side, root, c) for c in checks for side, root in (("here", ROOT), (ref, there)) if (root / located(c, root)).exists()] * runs
        with ThreadPoolExecutor(AT_ONCE) as pool:
            done = list(pool.map(lambda j: (j[0], run(j[2], j[1])), jobs))
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(there)], cwd=ROOT, capture_output=True)
    passed = {}
    for side, (check, code, _, _) in done:
        p, n = passed.get((side, check), (0, 0))
        passed[side, check] = (p + (not code), n + 1)
    width = max(len(c) for c in checks)
    print(f"\n{'check':{width}}  {'here':>6}  {ref:>6}")
    for c in checks:
        here, theirs = passed.get(("here", c), (0, 0)), passed.get((ref, c))
        worse = theirs and here[0] / here[1] < theirs[0] / theirs[1]
        print(f"{c:{width}}  {here[0]:>3}/{here[1]:<2}  " + (f"{theirs[0]:>3}/{theirs[1]:<2}" if theirs else "   new")
              + ("  <- fewer passes here" if worse else ""))


def main():
    args = sys.argv[1:]
    if "--compare" in args:
        i = args.index("--compare")
        ref = args[i + 1] if i + 1 < len(args) else "main"
        runs = int(args[args.index("--runs") + 1]) if "--runs" in args else RUNS
        rest = [a for j, a in enumerate(args) if j not in (i, i + 1) and a != "--runs" and (j == 0 or args[j - 1] != "--runs")]
        return compare(ref, rest, runs)
    checks = pick(args)
    if not checks:
        print("No checks cover what changed.")
        return
    t0 = time.monotonic()
    with ThreadPoolExecutor(AT_ONCE) as pool:
        results = list(pool.map(run, checks))
    failed, flaky = [], []
    for check, code, report, secs in results:
        if code and check in TALKER:
            again = run(check)
            if not again[1]:
                flaky.append((check, report))
                print(f"flaky  {check}  ({secs:.0f}s, passed on the retry)")
                continue
            report = again[2]
        print(f"{'ok' if not code else 'FAILED':6} {check}  ({secs:.0f}s)")
        if code:
            failed.append(check)
            print(report)
    for check, report in flaky:  # what failed the first time, for a look at what varies
        print(f"\n{check}, first run:\n" + "\n".join(l for l in report.splitlines() if l.startswith("FAIL")))
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed"
          + (f", {len(flaky)} on a retry" if flaky else "") + f", in {time.monotonic() - t0:.0f}s")
    if any(c in TALKER for c in checks) and prompt_changed("main"):
        print("\nThe talker's prompt or a tool's description differs from main: one pass proves little there. "
              "Compare pass rates over several runs with `uv run run_tests.py --compare main` (names narrow it).")
    sys.exit(1 if failed else 0)


main()
