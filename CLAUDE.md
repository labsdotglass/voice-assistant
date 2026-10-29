# voice-assistant: instructions for Claude

## Voice speaking-style requests

When the voice-mode plugin forwards "Update the voice assistant's instructions: …" (the user told the voice front how to talk; older versions said "the voice partner's"), treat it as a request to change the voice assistant's persistent instructions, not a one-off:

1. Edit `SYSTEM_PROMPT` in `talker.py`. A request about the voice itself (speed, voice) goes to Kokoro's settings in `daemon.py` instead; the voices offered in the orb's menu, and the Pass on my words modes, are in `settings.py`.
2. Run `uv run run_tests.py` (every check, in parallel; `--changed` for only what your edits affect while iterating), plus a targeted check of the new behavior; if it's worth keeping, add it to `tests/test_naming.py` or a new `tests/test_*.py` listed in `run_tests.py`.
3. Commit and push.
4. Restart voice so the daemon runs the new code: `pkill -f "python3 daemon.py --socket"`. The session that has voice starts it again within seconds (check `~/.voice-assistant/logs/voice.log` for "voice daemon starting").

## Releasing to other people

Others install voice-mode from the plugin marketplace, and `claude plugin update` only brings them a change when the version in `.claude-plugin/plugin.json` goes up. So with every pushed change they should get (anything outside tests and docs), bump that version in the same push: the minor number for a new feature or behavior, the patch number for a fix. They update as the README's "Updating" says.

## New features: what the checks can't catch

`uv run run_tests.py` checks that every orb menu item has a voice route (`test_parity.py`), that per-language tables cover every language (`test_translations.py`), and `githooks/pre-push` checks the version bump. Before shipping a feature, also go through what only judgment catches:

- **Voice:** can it be asked for out loud? A new menu item needs a `by_voice` route and a `MENU_ACTIONS` entry (the parity check fails until it has them), and a line in the voice commands modal once that exists.
- **The talker's prompt:** does `SYSTEM_PROMPT` need to mention it (as rule 3d does for the menu)? After changing the prompt or a tool's description, run `uv run run_tests.py --compare main <checks>`: one pass proves little.
- **Translations:** anything shown or spoken per language needs every language in `settings.LANGUAGES`.
- **The tour:** does it need a slide (`orb-page/src/tour.tsx`)?
- **README:** the feature, if users would want to know it (the menu table, Using it); how it works in `docs/architecture.md`.
- **UI:** a screenshot in dark mode on the card or PR.

## How changes reach running sessions

- The mod (the repo root: `.claude-plugin/plugin.json`, `hooks/register.tsx`) is loaded from `CLAUDE_CODE_PLUGIN_DIRS` in `~/.claude/settings.json`, and `CLAUDE_CODE_PLUGIN_DIR_WATCH=1` there makes every open session reload it when a file in it is saved (after the turn that edits it ends). No version bump is needed; a session started before that setting existed has to be reopened once. A reload resets the mod's own variables, including voice's queued prompts.
- The first run's window talks to the daemon through `~/.voice-assistant/first-run.json` (phase) and `first-run-request` (its answers), in `first_run.py`; its copy lives in `orb-page/src/first-run-text.json`, with every language (`test_translations.py` checks).
- The daemon and talker (`daemon.py`, `talker.py`, `focus.py`, `helper_transport.py`) only change when voice restarts (step 4).

These rules are for the voice front only. They change how the talker speaks, not how Claude writes in sessions.

How to write them:
- Describe the quality you want rather than giving example phrases. Haiku copies examples word for word (that's how every acknowledgment became "Sent.").
- The text a tool returns in `talker.py` is the strongest lever on what the talker says right after that tool call.
- The talker speaks to the user as "you", never "the user".
