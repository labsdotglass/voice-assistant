# How Voice Assistant works

```
mic → echo cancellation → Silero VAD + Smart Turn → Whisper large-v3 (MLX) → your voice? → talker (Claude Haiku) → Kokoro → speakers
```

Three parts run on your Mac:

- **The daemon** (`daemon.py`, Python, [Pipecat](https://github.com/pipecat-ai/pipecat) 1.13): the audio pipeline, the talker, and a Unix socket the sessions talk to. Started by the plugin on `/talk`, with uv, from the plugin's folder.
- **The audio helper** (`audio_helper/main.swift`, run through `Voice Assistant.app`): microphone and speakers through macOS voice processing (echo cancellation), and the floating orb.
- **The plugin** (`hooks/register.tsx`): `/talk` and the other commands, the Start voice button, the transcript panel, and the relay between each Claude Code session and the daemon.

## The talker

The talker (`talker.py`, Claude Haiku through your subscription) is the only voice you speak with. It sees a live feed of the session you're in (your prompts, its tool calls, plan updates, finished turns, questions) and, for each thing you say, decides whether to answer itself, send your words to the session (`send_to_session`, queued while the session is busy), or interrupt it (only when you ask). Its instructions are `SYSTEM_PROMPT`; the text each tool returns steers what it says next.

- **Follows you between sessions.** The desktop app records which session you focused last. `focus.py` watches those records, and voice talks to that session. Every session running the plugin registers with the daemon and long-polls it; only the focused one gets commands, and only its events reach the talker.
- **Progress.** The plugin passes the session's plan updates and what it writes mid-turn. A step change is spoken in a few words, at most every 5 seconds; the writing is background, kept to answer questions.
- **Queued prompts.** While the session is busy, the plugin holds voice's prompts itself and submits the next when a turn ends. You can take one back ("never mind the changelog one").
- **Other sessions.** When another session finishes a turn or waits for you, its plugin leaves a note in `~/.voice-assistant/run/`; voice mentions it and offers to go there. New sessions start through the CLI (`claude -p`, then `claude --desktop --resume` to open it in the app).
- **Ends on goodbye.** An explicit goodbye ends voice once the goodbye has played. Thanks alone doesn't; voice asks whether there's anything else. Voice also stops after 5 minutes of silence.
- **Your instructions about how it talks** are kept in `~/.voice-assistant/instructions.txt` (`remember_instruction`) and added to the talker's instructions at every start; `forget_instructions` clears them. They never go to the session, which is your own project.
- **Session text is news, not orders.** What the session writes can quote web pages and files, so the talker treats it as something to tell you about, never as instructions.

## Hearing you

- **Echo cancellation.** The helper uses macOS voice processing, so voice's own speech is removed from the mic and the mic stays open while it talks. `BargeInGate` (`helper_transport.py`) asks for louder, more confident speech while voice is speaking (the echo left after cancellation measured up to about 0.74). `HallucinationFilter` drops Whisper's usual inventions from silence ("thanks for watching").
- **End of turn.** Pipecat's Smart Turn model decides from how you sound whether you've finished, and waits out 3 seconds of silence when it thinks you haven't. A turn whose transcript ends with a question mark ends at once (`QuestionEndsTurn`), because some finished questions sound unfinished to it.
- **The draft line.** While you talk, a small CPU Whisper model streams a gray draft into the transcript panel about once a second (`interim.py`); the final pass replaces it.
- **Only your voice.** `speaker.py` checks each utterance against your voice print (ECAPA-TDNN from SpeechBrain) in about 20 ms, while Whisper transcribes, so it adds nothing to the reply time. Another voice is dropped before the talker hears it. The voice print is built from recordings in `~/.voice-assistant/live/<language>/`, taken from the phrases you read on the first run (or `/voice-enroll`). `SPEAKER_CUTOFF` (0.33) is the similarity at which it's you: measured on a MacBook mic, another person reached 0.32 from 1 second of speech, and at 0.33 a one-word answer of yours is missed about 16% of the time, another person's let through about 6%. Phrases under 0.6 s skip the check. Every check is logged as `[speaker]`.

## Speaking

Every voice is spoken by Kokoro on the GPU through MLX (`hebrew_voice.py`), so a reply's first sound is ready in about 0.1 s. If MLX fails, that piece is spoken by ONNX Kokoro. The talker's acknowledgment of a request is spoken from the tool call itself, as it streams.

Typical speed on Apple silicon: Whisper about 0.3 s for a short utterance; the talker's first word 1.2 to 1.9 s; Kokoro's first sentence about 0.26 s.

## Languages

`languages.json` is the one list of languages: each one's Whisper and Kokoro codes, the Hugging Face repos it downloads (exact bytes) and its download size. Switching to a language voice hasn't heard you in yet asks you to read that language's phrases, since the voice check scores lower in a language you didn't enroll in. Hebrew has its own models and reading rules: [hebrew.md](hebrew.md).

## The orb

`orb-page/` is a small Vite app that renders the orb (`AiAvatar`, WebGPU) on a transparent page, and builds the first run's window. The built pages (`orb-page/dist/orb.html`, `onboarding.html`) are committed, so running voice needs no Node. The helper loads each page as a string with an https base, because WebKit won't run module scripts from `file://` and WebGPU needs a secure context. The orb's state arrives on "S" messages from the daemon; session busy shows as three soft dots inside it.

A language switch restarts voice in place: the daemon re-executes itself and hands on the audio helper, the socket and the registered sessions, so the orb stays where it is with its spinner.

## Where things live

- `~/.voice-assistant/` (0700): settings, voice print and recordings, greetings, the first run's state, `run/` (the socket, other sessions' notes) and `logs/`.
- `bin/` in the plugin's folder: the audio helper, `Voice Assistant.app` and the first run's window, built on the first start.

## Files

- `daemon.py`: the voice process: audio pipeline, the socket, focus following, start and stop.
- `talker.py`: the talker, the session feed, the task board, greetings and goodbyes.
- `speaker.py`, `enroll_voice.py`: answering only your voice, and learning it.
- `settings.py`, `languages.json`: the orb menu's settings and the languages.
- `interim.py`: the draft line. `focus.py`: which session you're in. `new_session.py`: starting and finding sessions. `claude_login.py`: the subscription check.
- `helper_transport.py`, `audio_helper/main.swift`, `audio_helper/launcher.swift`: the audio helper and the app that runs it, so macOS asks for the microphone under its name.
- `onboarding.py`, `first_run.py`, `audio_helper/onboarding.swift`, `orb-page/src/tour.tsx`: the first run's window. Its words, in every language, are in `orb-page/src/first-run-text.json`.
- `hebrew_voice.py`, `hebrew_mlx.py`, `hebrew_terms.py`: Hebrew.
- `setup.py`: an optional check of what voice needs. `auto_mode.py`: reads and sets the default permission mode.
- `hooks/register.tsx`: the Claude Code plugin. `orb-page/`: the orb and the first run's window.

## What it relies on

A few parts of Claude Code and macOS that voice uses aren't documented, so an update to either can break it:

- `claude --desktop --resume <id>` opens a session in the desktop app (`new_session.py`).
- The desktop app's session records in `~/Library/Application Support/Claude/claude-code-sessions/` say which session you're in and what other sessions did.
- The CLI bundled with the Agent SDK checks your login (`claude_login.py`, `setup.py`).
- `responsibility_spawnattrs_setdisclaim`, a private macOS call, makes Voice Assistant.app the one macOS asks about the microphone (`audio_helper/launcher.swift`).

## Without echo cancellation

`--no-aec` falls back to PyAudio, with the mic muted while voice speaks (unless `--headphones` or `VOICE_HEADPHONES=1`). PyAudio isn't in the default install, since it builds against PortAudio: `brew install portaudio`, then `uv run --extra no-aec daemon.py --no-aec`.
