"""Enroll your voice, so voice mode answers only you (see speaker.py).

    uv run enroll_voice.py --live          # voice mode's own audio: talk to it for about 20 s of speech
    uv run enroll_voice.py my-voice.wav    # or a recording you already have (needs ffmpeg to read it)

The usual way is in the app: /voice-enroll, or Learn my voice again in the orb's menu (eight short phrases).

Saves the voice print to ~/.voice-assistant/voiceprint.npy (outside the repo). Restart voice
afterwards. Delete that file and voice mode answers everyone again.
"""

import shutil
import subprocess
import sys

import numpy as np

from speaker import CAPTURE_SECS, HOME, VOICEPRINT, VoicePrint, start_enrollment

def load(path: str) -> np.ndarray:
    if not shutil.which("ffmpeg"):
        sys.exit("Reading a recording needs ffmpeg (brew install ffmpeg). Or use --live, or /voice-enroll in the app.")
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def live():
    """Hands enrollment to the running voice daemon: it keeps your next utterances, as it hears them."""
    import settings

    start_enrollment(settings.load()["language"])  # in the language you talk in, from the orb's menu
    print(f"Live enrollment on. With voice mode running, talk to it for about {CAPTURE_SECS} s; it stays silent")
    print("meanwhile and switches to the new voice print by itself. Progress: [speaker] lines in ~/.voice-assistant/logs/voice.log.")


def main():
    if sys.argv[1:] == ["--live"]:
        return live()
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    audio = load(sys.argv[1])
    # Keep only the parts with speech in them: 0.5 s windows clearly above the room's noise floor
    # (the quietest few, since a reading has few pauses).
    win = 8000
    chunks = [audio[i:i + win] for i in range(0, len(audio) - win, win)]
    level = np.array([np.sqrt(np.mean(c ** 2)) + 1e-9 for c in chunks])
    floor = np.percentile(level, 3)
    speech = [c for c, v in zip(chunks, level) if 20 * np.log10(v / floor) > 12]
    secs = len(speech) * win / 16000
    if secs < 8:
        sys.exit(f"Only {secs:.0f} s of speech found; need at least 8. Speak closer, or raise the mic's input volume.")
    HOME.mkdir(exist_ok=True)
    vp = VoicePrint()
    np.save(VOICEPRINT, vp.make([np.concatenate(speech)]).numpy())
    print(f"Voice print saved to {VOICEPRINT}, from {secs:.0f} s of speech. Restart voice to use it.")


if __name__ == "__main__":
    main()
