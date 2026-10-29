"""The release check, run by githooks/pre-push: a push that changes what users get must bump the version.

Others install voice-mode from the plugin marketplace, and `claude plugin update` only brings them a
change when the version in .claude-plugin/plugin.json goes up (CLAUDE.md, "Releasing to other people").
So a push whose commits change files outside tests, docs and dev tooling, without raising that version,
is stopped. Git hands pre-push one line per ref on stdin: <local ref> <local sha> <remote ref> <remote sha>.
Skip once with `git push --no-verify`.
"""

import json
import re
import subprocess
import sys

PLUGIN = ".claude-plugin/plugin.json"
ZERO = "0" * 40
# What users don't get a change from: tests, docs and the tooling that only runs here.
NOT_SHIPPED = (r"tests/.*", r"test_[^/]*\.py", r"run_tests\.py", r"release_check\.py", r".*\.md",
               r"githooks/.*", r"\.github/.*", r"\.claude/.*", r"\.gitignore", r"orb_demo\.py")


def shipped(files: list[str]) -> list[str]:
    return [f for f in files if not any(re.fullmatch(p, f) for p in NOT_SHIPPED)]


def version(text: str | None) -> tuple[int, ...]:
    return tuple(int(n) for n in json.loads(text)["version"].split(".")) if text else ()


def problem(files: list[str], before: str | None, after: str | None) -> str | None:
    """Why this push can't go, or None: files it changes and plugin.json's text before and after it."""
    users_get = shipped(files)
    if not users_get or version(after) > version(before):
        return None
    return (f"These changes reach users ({', '.join(users_get[:5])}{', ...' if len(users_get) > 5 else ''}), "
            f"but the version in {PLUGIN} didn't go up.\n"
            "Others only get a change through `claude plugin update` when that version rises: bump the minor "
            "number for a new feature or behavior, the patch number for a fix, in the same push (CLAUDE.md, "
            "\"Releasing to other people\"). Push without the check once with `git push --no-verify`.")


def git(*args: str) -> str | None:
    done = subprocess.run(["git", *args], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def main():
    failed = False
    for line in sys.stdin.read().splitlines():
        local_ref, local, remote_ref, remote = line.split()
        if local == ZERO:  # deleting a branch
            continue
        base = remote if remote != ZERO and git("cat-file", "-e", remote) is not None else \
            (git("merge-base", local, "origin/main") or "").strip()
        if not base:
            continue
        files = (git("diff", "--name-only", base, local) or "").split()
        why = problem(files, git("show", f"{base}:{PLUGIN}"), git("show", f"{local}:{PLUGIN}"))
        if why:
            print(f"pre-push: {remote_ref}: {why}", file=sys.stderr)
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
