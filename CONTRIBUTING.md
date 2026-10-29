# Contributing

Thanks for helping. Voice mode runs on macOS with Apple silicon, inside the Claude desktop app; see the README's Requirements. [docs/architecture.md](docs/architecture.md) explains how the parts fit together.

## Set up a clone

1. `git clone`, then `uv sync` in the clone.
2. Install the git hooks once: `git config core.hooksPath githooks`. Before each commit they run the fast checks, and before each push they check the version bump.
3. Load the mod from your clone instead of the installed plugin: in `~/.claude/settings.json`, set `env.CLAUDE_CODE_PLUGIN_DIRS` to the clone's path and `CLAUDE_CODE_PLUGIN_DIR_WATCH=1`. Every save of `hooks/register.tsx` then reloads the mod in open sessions.
4. To type-check the mod: Claude Code writes its plugin API types to `.claude-plugin/types/` (not committed) when it loads the mod from your clone. After that, `npx -p typescript tsc --noEmit -p .` checks `hooks/register.tsx`.
5. The daemon and talker (`daemon.py`, `talker.py` and the files they import) only change when voice restarts: `pkill -f "python3 daemon.py --socket"`, and the session that had voice starts it again. Its log is `~/.voice-assistant/logs/voice.log`.

## Checks

- `uv run run_tests.py --offline`: the fast checks, no model calls (also the pre-commit hook). Some of them time speech, so they can fail on a busy Mac; run a failed one on its own before digging in.
- `uv run run_tests.py --no-models`: the offline checks that download nothing either. CI runs these on every pull request (`.github/workflows/checks.yml`), and builds the Swift programs.
- `uv run run_tests.py`: everything, including the checks that call the talker's model (Haiku). These need a Claude subscription and take a few minutes; a check that fails once and passes on retry is reported as flaky.
- `uv run run_tests.py --changed` runs only the checks your uncommitted changes can affect; names pick checks (`uv run run_tests.py form confirm`).
- After changing the talker's prompt (`SYSTEM_PROMPT`) or a tool's description, one pass proves little: run `uv run run_tests.py --compare main <checks>` (6 runs a side; `--runs N` for more) and compare the pass rates.
- After editing `hooks/register.tsx`, run `claude plugin validate .`: the type check doesn't catch everything the loader refuses.

The checks are in `tests/`; run one on its own with `uv run tests/test_naming.py`. A new check starts with `import _root` (the repo's modules on the import path) and goes in `OFFLINE` or `TALKER` in `run_tests.py` (an offline check that needs the speech models on disk also goes in `NEEDS_MODELS`, which CI skips). Each check's docstring says what it covers. `tests/test_speaker.py` also scores real voices if you put recordings of yourself in `spike/speakers/me/` and of others in `spike/speakers/other/` (never committed).

## The orb and the first run's window

The pages in `orb-page/dist/` are committed. To change the orb or the first run's screens, rebuild them:

```bash
cd orb-page
npm install
npm run build
```

`uv run orb_demo.py` (with voice running) drops the orb at spots around the screen, to check its edge snapping.

## Languages and models

`languages.json` lists each language's models with their exact download size. After changing a model, measure it with `uv run python -c "from huggingface_hub import HfApi as A; print(sum(s.size for s in A().model_info('REPO', files_metadata=True).siblings))"` (count only the files the code fetches) and update the bytes and the total.

## Experiments

`touch ~/.voice-assistant/early-start` makes voice start a reply's first sentence at its first pause instead of its end: speech starts sooner, sometimes with a slight pause. Delete the file to turn it off.

## Changing how the talker speaks

- Describe the quality you want rather than giving example phrases: the talker's model tends to copy examples word for word.
- The text a tool returns is the strongest lever on what the talker says right after calling it.
- The talker speaks to the user as "you", never "the user".

## New features

- A new item in the orb's menu needs a voice route too (`by_voice` in `daemon.py`, `MENU_ACTIONS` in `talker.py`); `test_parity.py` fails until it has one.
- Anything shown or spoken per language needs every language in `settings.LANGUAGES` (`test_translations.py`).
- Consider whether the tour (`orb-page/src/tour.tsx`), the README and `docs/architecture.md` need a line.

## Releasing

Users get a change through `claude plugin update` only when the version in `.claude-plugin/plugin.json` goes up. Bump it in the same push as any change users get (anything outside tests and docs): the minor number for a new feature or behavior, the patch number for a fix. The pre-push hook checks it. Contributing from outside? Leave the version alone and push with `--no-verify`: the maintainer bumps it when merging.
