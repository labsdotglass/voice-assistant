"""The release check (no model): a push that changes what users get without a version bump is stopped,
and the commit and push hooks are installed where git looks for them."""

import _root  # noqa: F401  (the repo root on the import path)
import os
import subprocess
import sys
from pathlib import Path

from release_check import problem

ROOT = Path(__file__).resolve().parent.parent
results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def plugin(v):
    return '{ "name": "voice-mode", "version": "%s" }' % v


def main():
    ok(problem(["talker.py"], plugin("0.29.0"), plugin("0.29.0")), "a talker change with the same version is stopped")
    ok(problem(["hooks/register.tsx", "README.md"], plugin("0.29.0"), plugin("0.28.9")), "a version that went down is stopped")
    ok(not problem(["talker.py", ".claude-plugin/plugin.json"], plugin("0.29.0"), plugin("0.29.1")), "a bumped patch goes")
    ok(not problem(["daemon.py"], plugin("0.29.9"), plugin("0.30.0")), "a bumped minor goes (compared as numbers)")
    ok(not problem(["tests/test_parity.py", "test_parity.py", "run_tests.py", "README.md", "CLAUDE.md", "githooks/pre-push", ".github/workflows/checks.yml", ".claude/board.json"],
                   plugin("0.29.0"), plugin("0.29.0")), "tests, docs and tooling need no bump")
    ok(problem([".claude-plugin/plugin.json"], plugin("0.29.0"), plugin("0.29.0")), ".claude-plugin isn't mistaken for .claude/ tooling")
    why = problem(["settings.py"], plugin("0.29.0"), plugin("0.29.0")) or ""
    ok("minor" in why and "patch" in why and "--no-verify" in why, "the refusal gives CLAUDE.md's rule and the way past it")

    # The hooks run: core.hooksPath leads to a githooks/ that exists. After the folder rename it still named the
    # old folder and every commit skipped its checks; `git config core.hooksPath githooks` (relative) can't go stale.
    # (The desktop app's worktrees set their own, absolute, to the main checkout's githooks/.)
    hooks = subprocess.run(["git", "config", "core.hooksPath"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    ok(hooks and (ROOT / hooks).name == "githooks" and (ROOT / hooks / "pre-commit").exists(),
       f"git runs the hooks in githooks/ (core.hooksPath is {hooks!r}; set it with `git config core.hooksPath githooks`)")
    for hook in ("pre-commit", "pre-push"):
        ok(os.access(ROOT / "githooks" / hook, os.X_OK), f"githooks/{hook} is there and runnable")

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


main()
