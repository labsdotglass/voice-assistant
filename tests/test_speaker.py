"""Only the enrolled voice is answered: SpeakerCheck + SpeakerGate on real Pipecat frames (no mic, no model calls).

A Kokoro voice is enrolled; its own sentence goes through, another voice's is dropped, each
check timed. With the local recordings in spike/speakers/ (never committed), the same runs on
real voices: you enrolled, then you and another person speaking (folders `me/` and `other/`).
"""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import torch
import torchaudio
from pipecat.frames.frames import InputAudioRawFrame, TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.tests.utils import run_test

from voices import kokoro
from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX

from helper_transport import HallucinationFilter, WhisperKeepingYou, kept_text
import speaker
from speaker import CUTOFF, SpeakerCheck, SpeakerGate, Verdict, VoicePrint

SPEAKERS = Path(__file__).resolve().parent.parent / "spike/speakers"
results = []


def ok(cond, msg):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + msg)


def to16k(audio: np.ndarray, sr: int) -> np.ndarray:
    return torchaudio.functional.resample(torch.from_numpy(audio).float(), sr, 16000).numpy()


def frames_for(audio: np.ndarray, text: str) -> list:
    """One utterance as the pipeline sees it: speech start, 20 ms mic frames, speech end, then Whisper's text."""
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
    step = 640  # 20 ms at 16 kHz, 16-bit
    audio_frames = [InputAudioRawFrame(audio=pcm[i:i + step], sample_rate=16000, num_channels=1) for i in range(0, len(pcm), step)]
    return [VADUserStartedSpeakingFrame(), *audio_frames, VADUserStoppedSpeakingFrame(), TranscriptionFrame(text, "", "")]


ENROLLED = []  # each time a live enrollment finished (the daemon's onboarding callback)


async def heard(voice: VoicePrint, audio: np.ndarray, text: str = "said") -> bool:
    """Whether Whisper's text for this utterance gets past the gate and the hallucination filter, as in the daemon."""
    verdict = Verdict()
    check = SpeakerCheck(voice, verdict, on_enrolled=lambda: ENROLLED.append(True))
    pipeline = Pipeline([check, SpeakerGate(verdict), HallucinationFilter(verdict)])
    down, _ = await run_test(pipeline, frames_to_send=frames_for(audio, text))
    return any(isinstance(f, TranscriptionFrame) for f in down)


async def main():
    voice = VoicePrint()
    k = kokoro()
    say = lambda v, text: to16k(*k.create(text, voice=v, speed=1.0, lang="en-us"))

    voice.print = voice.make([say("am_adam", "Hi, this is my voice. I'm setting up my character before the match starts.")])
    ok(len(voice.print) > 5, f"the voice print is many half-second pieces ({len(voice.print)})")
    ok(await heard(voice, say("am_adam", "Go left, there's someone behind the wall.")), "the enrolled voice is answered")
    ok(not await heard(voice, say("bf_emma", "Go left, there's someone behind the wall.")), "another voice is not answered")
    ok(await heard(voice, say("am_adam", "Yes, do it.")), "a short answer from the enrolled voice is answered")
    ok(not await heard(voice, say("bf_emma", "Yes, do it.")), "the same short answer from another voice is not")
    ok(await heard(voice, say("bf_emma", "Go.")[:8000]), "a blip too short to judge (0.5 s) is let through")

    # Words Whisper invents from silence or echo ("okay", "thank you") are dropped, unless the voice
    # check matched the utterance to you: then it's really you saying them.
    ok(await heard(voice, say("am_adam", "Okay, sure."), " Okay. "), "your own \"okay\" gets through")
    ok(not await heard(voice, say("am_adam", "Okay.")[:8000], " Okay. "), "an \"okay\" too short to judge is still dropped")
    enrolled, voice.print = voice.print, None
    ok(not await heard(voice, say("am_adam", "Thank you, sure."), " Thank you. "), "with no voice print, \"thank you\" is still dropped")
    voice.print = enrolled

    # Whisper rates a lone short word as likely silence (no_speech_prob 0.6 or more): pipecat drops it,
    # unless the voice check matched the utterance to you.
    segs = [{"text": " Yes.", "no_speech_prob": 0.72, "compression_ratio": 0.33}]
    ok(kept_text(segs, 0.6, you=True) == "Yes.", "a word Whisper rates as silence is kept when the voice check matched you")
    ok(kept_text(segs, 0.6, you=False) == "", "and dropped when it didn't")
    ok(kept_text([{"text": " Go on.", "no_speech_prob": 0.1, "compression_ratio": 0.5}], 0.6, you=False) == "Go on.", "clear speech is kept either way")
    verdict = Verdict()
    verdict.matched = True
    stt = WhisperKeepingYou(verdict, settings=WhisperSTTServiceMLX.Settings(model=MLXModel.LARGE_V3.value))
    pcm = (np.clip(say("am_adam", "Yes."), -1, 1) * 32767).astype(np.int16).tobytes()
    out = [f async for f in stt.run_stt(pcm)]
    ok([f.text.strip() for f in out if isinstance(f, TranscriptionFrame)] == ["Yes."], f"Whisper runs through it: {[getattr(f, 'text', getattr(f, 'error', f)) for f in out]}")

    # Live enrollment (enroll_voice.py --live), in a temp folder: answers nobody while it captures,
    # then the captured voice is the one answered.
    tmp = Path(tempfile.mkdtemp())
    speaker.CAPTURE, speaker.LIVE, speaker.VOICEPRINT = tmp / "capture", tmp / "live", tmp / "voiceprint.npy"
    speaker.start_enrollment()  # what the daemon does on a first start with no voice print
    lines = [
        "Okay, I'm here. Can you hear me? I'm setting up my character before the match, so give me a second.",
        "Go left. There's someone behind the wall. Wait for me at the bridge, I'm almost there.",
        "I need help over here, I'm low on health. Nice shot! That was close.",
        "Let's check the map first, then we push together through the north gate. Alright, I'm ready when you are.",
        "One more thing before we start: keep the voice chat clear during the round, and call out what you see.",
    ]
    speaker.QUIET_SECS = 0.3
    answered = []
    for line in lines:
        answered.append(await heard(voice, say("bm_george", line)))
        await asyncio.sleep(1.5)  # a pause longer than the quiet spell: the reading is over, if there's enough
    done_at = answered.index(True) if True in answered else len(answered)  # the first line after the capture filled up
    ok(done_at >= 3 and all(answered[done_at:]), f"while it captures, nothing is answered (the first {done_at} lines)")
    ok(not speaker.CAPTURE.exists() and speaker.VOICEPRINT.exists(), "after ~20 s and a pause it saves the new voice print and stops capturing")
    ok(ENROLLED == [True], "and tells the daemon once, so voice says it's ready")

    # Past the 20 s it waits for you to finish reading the card: a sentence begun within the quiet
    # spell keeps it open, and is captured too.
    george = voice.print
    pcm = lambda a: (np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes()
    sentence = pcm(say("bm_george", lines[3]))
    reading = []
    speaker.start_enrollment()
    check = SpeakerCheck(voice, Verdict(), on_enrolled=lambda: reading.append("done"))
    while not check._finishing:  # sentences until there's enough
        await check._check(sentence, 16000)
    check.speech_started()  # the next sentence of the card begins
    await asyncio.sleep(speaker.QUIET_SECS + 1)
    ok(reading == [] and speaker.CAPTURE.exists(), "with enough speech, it still waits while you read on")
    check._speaking = False
    await check._check(sentence, 16000)
    captured = len(list(speaker.LIVE.rglob("*.wav")))
    await asyncio.sleep(speaker.QUIET_SECS + 1.5)
    ok(reading == ["done"] and not speaker.CAPTURE.exists(), "and ends once you've been quiet after the last sentence")
    ok(len(list(speaker.LIVE.rglob("*.wav"))) == captured, "with that last sentence in the voice print")

    speaker.CAPTURE_MAX_SECS = 2 * len(sentence) / 32000 - 0.1
    reading.clear()
    speaker.start_enrollment()
    check = SpeakerCheck(voice, Verdict(), on_enrolled=lambda: reading.append("done"))
    speaker.CAPTURE_SECS, min_secs = 1, speaker.CAPTURE_SECS
    await check._check(sentence, 16000)
    check.speech_started()  # reading on and on
    check._speaking = False
    await check._check(sentence, 16000)
    ok(reading == ["done"], "someone who never pauses is stopped at the limit")
    speaker.CAPTURE_SECS, speaker.CAPTURE_MAX_SECS = min_secs, 45
    voice.print = george
    ok(await heard(voice, say("bm_george", "Go right, they're on the roof.")), "the captured voice is answered right away")
    ok(not await heard(voice, say("am_adam", "Go right, they're on the roof.")), "the voice enrolled before isn't any more")

    # A single-row print saved before the pieces is rebuilt from the live enrollment that made it.
    import os

    import soundfile as sf

    pieces_print = voice.make([sf.read(f, dtype="float32")[0] for f in sorted(speaker.LIVE.rglob("*.wav"))])
    np.save(speaker.VOICEPRINT, voice.embed(say("bm_george", lines[0])).numpy())
    old = VoicePrint()
    ok(len(old.print) == 1 and old.score(say("bm_george", "Go right.")) > -1, "an old single-row print still loads and scores")
    ok(old.upgrade() and len(old.print) == len(pieces_print), "and is rebuilt as pieces from the enrollment recordings")
    ok(len(np.load(speaker.VOICEPRINT)) == len(pieces_print) and not old.upgrade(), "saved, so the next start leaves it alone")
    np.save(speaker.VOICEPRINT, voice.embed(say("bm_george", lines[0])).numpy())
    t = speaker.VOICEPRINT.stat().st_mtime + 3600
    os.utime(speaker.VOICEPRINT, (t, t))  # made later, from something else (enroll_voice.py with a file)
    ok(not VoicePrint().upgrade(), "a print the recordings didn't make is left as it is")

    me, other = SPEAKERS / "me/enroll-macbook.wav", SPEAKERS / "other/enroll-macbook.wav"
    if me.exists() and other.exists():
        import soundfile as sf

        a, sr = sf.read(me, dtype="float32")
        b, _ = sf.read(other, dtype="float32")
        voice.print = voice.make([a[sr:16 * sr]])
        t0 = time.perf_counter()
        score = voice.score(a[20 * sr:24 * sr])
        ms = (time.perf_counter() - t0) * 1000
        ok(score >= CUTOFF and await heard(voice, a[20 * sr:24 * sr]), f"real: you are answered (similarity {score:.2f}, {ms:.0f} ms)")
        score = voice.score(b[2 * sr:6 * sr])
        ok(score < CUTOFF and not await heard(voice, b[2 * sr:6 * sr]), f"real: another person is not (similarity {score:.2f})")
        ok(ms < 100, f"real: a 4 s check takes {ms:.0f} ms, well under Whisper's ~300 ms")
    else:
        print("      (no local recordings in spike/speakers/: real-voice checks skipped)")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
