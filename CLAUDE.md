# voice-assistant: instructions for Claude

## Voice speaking-style requests

Voice keeps what a user tells it about how to talk in their own `~/.voice-assistant/instructions.txt` (`remember_instruction` in `talker.py`); nothing reaches the session. When you're asked to make such a change part of voice itself, for everyone (or a session receives an "Update the voice assistant's instructions: …" request from an older version), change the voice assistant's instructions:

1. Edit `SYSTEM_PROMPT` in `talker.py`. A request about the voice itself (speed, voice) goes to Kokoro's settings in `daemon.py` instead; the voices offered in the orb's menu, and the Pass on my words modes, are in `settings.py`.
2. Run `uv run run_tests.py` (every check, in parallel; `--changed` for only what your edits affect while iterating), plus a targeted check of the new behavior; if it's worth keeping, add it to `tests/test_naming.py` or a new `tests/test_*.py` listed in `run_tests.py`.
3. Commit and push.
4. Voice runs the installed plugin, not this checkout (see "How changes reach running sessions"): to hear the change before it's merged, `./staging.sh on` and restart the Claude app; once merged, update the plugin. `pkill -f "python3 daemon.py --socket"` restarts the daemon from whatever is installed (the session that has voice starts it again within seconds; `~/.voice-assistant/logs/voice.log`'s "voice daemon starting" line says which version, and "STAGING" with the branch for a staging install).

## Releasing to other people

Others install voice-mode from the plugin marketplace, and `claude plugin update` only brings them a change when the version in `.claude-plugin/plugin.json` goes up. So with every pushed change they should get (anything outside tests and docs), bump that version in the same push: the minor number for a new feature or behavior, the patch number for a fix. They update as the README's "Updating" says.

Nothing reaches `main` untried (card #43). A ruleset on `main` requires a green `staging` commit status on the PR's head commit, and only `./staging.sh pass` posts it. The loop: commit and push the branch (version bump included), `./staging.sh on <branch>`, restart the Claude app and try it, then `./staging.sh pass`, then merge. Run `pass` only when the person says it works, never on your own; a new commit (a fix, a bump) needs `on` and `pass` again. A commit has to be on GitHub to be marked: push before `on`, or push and then `pass`.

## New features: what the checks can't catch

`uv run run_tests.py` checks that every orb menu item has a voice route (`test_parity.py`), that per-language tables cover every language (`test_translations.py`), and `githooks/pre-push` checks the version bump. Before shipping a feature, also go through what only judgment catches:

- **Voice:** can it be asked for out loud? A new menu item needs a `by_voice` route and a `MENU_ACTIONS` entry (the parity check fails until it has them), and a line in the voice commands modal once that exists.
- **The talker's prompt:** does `SYSTEM_PROMPT` need to mention it (as rule 3d does for the menu)? After changing the prompt or a tool's description, run `uv run run_tests.py --compare main <checks>`: one pass proves little.
- **Translations:** anything shown or spoken per language needs every language in `settings.LANGUAGES`.
- **The tour:** does it need a slide (`orb-page/src/tour.tsx`)?
- **README:** the feature, if users would want to know it (the menu table, Using it); how it works in `docs/architecture.md`.
- **UI:** a screenshot in dark mode on the card or PR.

## How changes reach running sessions

- Voice runs from the installed plugin, `~/.claude/plugins/cache/<marketplace>/voice-mode/<version>/`, never from this checkout: the mod starts the daemon from its own folder (`$.plugin.root`, `hooks/register.tsx`). The release comes from the `voice-assistant` marketplace, which follows GitHub `main`: a merged, version-bumped change reaches you with `claude plugin marketplace update voice-assistant`, `claude plugin update voice-mode@voice-assistant` and a restart of the Claude app (Cmd-Q).
- **Staging**, to try a branch in the real app before merging: `./staging.sh on [branch]` installs the branch from a second marketplace, `voice-assistant-staging` in `~/.voice-assistant/staging` (a worktree of the branch, its version marked `-staging.<commit>`), and disables the release; `./staging.sh off` switches back; `./staging.sh status` says which runs. Restart the Claude app after either. Both share `~/.voice-assistant` (settings, voice prints, the built app and its microphone permission, the Python packages), so only one runs at a time. The Voice panel's title shows the running version: "Voice · 0.49.3" for the release, "Voice · 0.49.3 staging" while staging runs. Commit first: only committed work is staged.
- The first run's window talks to the daemon through `~/.voice-assistant/first-run.json` (phase) and `first-run-request` (its answers), in `first_run.py`; its copy lives in `orb-page/src/first-run-text.json`, with every language (`test_translations.py` checks).
- The daemon and talker (`daemon.py`, `talker.py`, `focus.py`, `helper_transport.py`) only change when voice restarts.

These rules are for the voice front only. They change how the talker speaks, not how Claude writes in sessions.

How to write them:
- Describe the quality you want rather than giving example phrases. Haiku copies examples word for word (that's how every acknowledgment became "Sent.").
- The text a tool returns in `talker.py` is the strongest lever on what the talker says right after that tool call.
- The talker speaks to the user as "you", never "the user".
