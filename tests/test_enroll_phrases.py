"""Learning your voice by phrases (no model): a phrase is checked when Whisper's text matches it, or after a few
tries on audio length alone; a fast reader gets one more phrase until there's speech enough for the voice print;
every language has its phrases, the Hebrew ones gender-neutral; and EnrollPhrases, after Whisper, keeps only the
audio of recognized phrases and writes the window's state as it goes. Everything in a temp folder.
"""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
from pipecat.frames.frames import TranscriptionFrame

import first_run
import settings
import speaker
from speaker import EXTRA_PHRASES, EnrollPhrases, PhraseTracker, Verdict, matches, phrases

results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


def test_matches_tolerates_whisper():
    ok(matches("please open the project and run the test", "Please open the project and run the tests."),
       "Whisper's lower case, missing period and singular still match the phrase")
    ok(matches("Hi it's me, yes, no, do it. Stop", "Hi, it's me. Yes. No. Do it. Stop."), "punctuation doesn't matter")
    ok(matches("open the project and run tests", "Please open the project and run the tests."), "a word or two missed still matches")
    ok(matches("אפשר לפתוח את הפרויקט ולהריץ את הבדיקות", "אפשר לפתוח את הפרויקט ולהריץ את הבדיקות?"), "Hebrew matches")


def test_rejects_other_text():
    ok(not matches("what's the weather like today", "Please open the project and run the tests."), "other words don't match")
    ok(not matches("the tests run and the project open please", "Please open the project and run the tests."), "the phrase's words out of order don't")
    ok(not matches("", "Let's get started."), "nothing heard doesn't")
    ok(not matches("project", "Please open the project and run the tests."), "one of its words isn't enough")


def test_phrase_accepted_after_tries():
    t = PhraseTracker(["Hi, it's me. Yes. No. Do it. Stop.", "Let's get started."])
    ok(t.heard("it's me", 2.0) is None and t.heard("", 2.0) is None, "a phrase not recognized stays open")
    ok(t.state()["current"] == 0, "and stays the one to read")
    ok(t.heard("yes", 2.0) == 0, "on the third try it's accepted on audio length alone")
    ok(t.state()["phrases"][0]["done"] and t.state()["current"] == 1, "checked, and the next one is current")
    t = PhraseTracker(["Hi, it's me. Yes. No. Do it. Stop."])
    for _ in range(3):
        got = t.heard("", 0.2)
    ok(got is None, "three blips too short to be the phrase don't check it")
    t = PhraseTracker(["Let's get started.", "Then fix it and show me the change."])
    ok(t.heard("then fix it and show me the change", 2.0) == 1, "a phrase read out of order is checked too")
    ok(t.state()["current"] == 0, "and the first open one is still current")
    t = PhraseTracker(["Let's get started. Please open the project and run the tests."])
    ok(t.heard("let's get started", 1.0) is None and t.heard("please open the project and run the tests", 2.0) == 0,
       "a phrase said in two pieces (a pause splits it) is checked once both are heard")


def test_other_text_is_no_try():
    # Other speech, too long or too short to be the phrase (it's about 3 s aloud): sharing only little words
    # like "the" with it doesn't make it a try.
    t = PhraseTracker(["Please open the project and run the tests."])
    for line, secs in (("what time is the meeting", 0.6), ("turn the volume down", 9.0), ("which one did you mean", 12.0)):
        ok(t.heard(line, secs) is None and not t.aimed, f"other text isn't a try at the phrase: {line!r} ({secs} s)")
    ok(not t.state()["phrases"][0]["done"], "three of them don't check it on length")
    ok(t.heard("so I was telling her about the weekend and how the whole trip went wrong from the start", 20.0) is None
       and not t.aimed, "a long unrelated sentence isn't a try either")


def test_short_words_said_apart():
    # The first phrase is short words: said with pauses, each piece is too short to be a try on its length and its
    # words too short to count as shared, yet the pieces together are the phrase.
    for lang, pieces in (("en", [("Hi, it's me.", 0.9), ("Yes.", 0.4), ("No.", 0.4), ("Do it.", 0.5), ("Stop.", 0.4)]),
                         ("he", [("היי, זה אני.", 0.9), ("כן.", 0.4), ("לא.", 0.4), ("יאללה.", 0.5), ("רגע, די.", 0.6)])):
        t = PhraseTracker(phrases(lang))
        for n, (text, secs) in enumerate(pieces, 1):
            if t.heard(text, secs) == 0:
                break
        ok(t.state()["phrases"][0]["done"], f"{lang}: the first phrase said with pauses is checked")
        ok(t.kept == n and n > 1, f"{lang}: the pieces that made it ({n}) are all kept for the voice print")


def test_other_hebrew_is_no_try():
    t = PhraseTracker([phrases("he")[1]])
    for _ in range(3):
        ok(t.heard("שמתי את הספר של אחותי על השולחן", 9.0) is None and not t.aimed,
           "a Hebrew sentence sharing only את with the phrase, at the wrong length, isn't a try")
    ok(not t.state()["phrases"][0]["done"], "nor are three of them")


def test_mangled_phrase_still_accepted():
    # A heavy accent: Whisper writes none of the phrase's words, but each utterance is about the phrase's length.
    t = PhraseTracker(["Please open the project and run the tests."])
    got = [t.heard(line, 3.0) for line in ("bliss oven to prodigy", "lease hope in her jacket", "plea zoe pen rocket")]
    ok(got == [None, None, 0] and t.kept == 3, f"three mangled tries of a plausible length check it, all three kept ({got}, {t.kept})")


def test_extra_phrase_when_short():
    t = PhraseTracker(["Let's get started.", "That looks good to me."], extra=["Run it again and tell me if it passes."])
    t.heard("let's get started", 1.0)
    t.heard("that looks good to me", 1.0)
    state = t.state()
    ok(len(state["phrases"]) == 3 and state["phrases"][2]["text"] == "Run it again and tell me if it passes.",
       f"all checked with {t.secs:.0f} s of speech (< {speaker.CAPTURE_SECS}): one more phrase ({len(state['phrases'])})")
    ok(not t.done and state["current"] == 2, "and it isn't done yet")
    t.heard("run it again and tell me if it passes", speaker.CAPTURE_SECS)
    ok(t.done and len(t.state()["phrases"]) == 3, "with enough speech after it, it's done, nothing more added")
    t = PhraseTracker(["Let's get started."], extra=["Run it again."])
    t.heard("let's get started", speaker.CAPTURE_SECS + 1)
    ok(t.done and len(t.state()["phrases"]) == 1, "a reader with speech enough gets no extra phrase")


MASCULINE_ONLY = {"תעשה", "בוא", "עצור", "תגיד", "תפתח", "תריץ", "תתקן", "תראה", "תבדוק"}


def test_phrases_every_language():
    for code, *_ in settings.LANGUAGES:
        lines = phrases(code)
        ok(len(lines) >= 6 and all(lines) and bool(EXTRA_PHRASES.get(code)), f"{code}: {len(lines)} phrases, and extras")
        ok(all(len(line.split()) <= 14 for line in lines + EXTRA_PHRASES[code]), f"{code}: every phrase is short")
    if "en" in EXTRA_PHRASES:
        ok(phrases("en") != phrases("he"), "each language has its own")
    hebrew = phrases("he") + EXTRA_PHRASES["he"]
    words = {w for line in hebrew for w in re.findall(r"[א-ת]+", line)}
    ok(not words & MASCULINE_ONLY, f"the Hebrew phrases address nobody as a man ({sorted(words & MASCULINE_ONLY)})")


class FakeVoice:
    """VoicePrint's capture and finish, without ECAPA: what's kept is counted, and a file stands for each."""

    def __init__(self):
        self.kept, self.finished = [], False

    def capture(self, audio, folder=None):
        self.kept.append(len(audio))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{len(self.kept)}.wav").write_bytes(b"")
        return sum(self.kept) / 16000

    def finish(self):
        self.finished = True
        speaker.CAPTURE.unlink(missing_ok=True)


class FakeCheck:
    level, _speaking = 0.5, False


async def test_enroll_phrases_keeps_recognized(tmp: Path):
    speaker.CAPTURE, speaker.LIVE, speaker.VOICEPRINT = tmp / "capture", tmp / "live", tmp / "voiceprint.npy"
    speaker.STAGED, speaker.SKIPPED = tmp / "live-new", tmp / "voice-skipped"
    first_run.STATE = tmp / "first-run.json"
    (speaker.LIVE / "en").mkdir(parents=True)
    (speaker.LIVE / "en" / "old.wav").write_bytes(b"")
    speaker.SKIPPED.touch()
    voice, verdict, done = FakeVoice(), Verdict(), []
    enroll = EnrollPhrases(voice, verdict, FakeCheck(), on_enrolled=lambda: done.append(True))
    enroll.start("en")
    state = json.loads(first_run.STATE.read_text())
    ok(state["phase"] == "voice" and state["current"] == 0 and len(state["phrases"]) == len(phrases("en")) and "level" in state and state["hint"] in ("hearing", "louder", "keep_going"),
       f"starting writes the phrases for the window ({state.get('phase')}, {len(state.get('phrases', []))} phrases)")
    ok(speaker.CAPTURE.exists(), "and turns on the capture")
    enroll.start("en")
    ok(enroll.tracker.state()["current"] == 0 and speaker.CAPTURE.exists(), "a second start (the window's enroll after /voice-enroll's) changes nothing")
    speech = (np.sin(np.arange(32000) / 5) * 0.3).astype(np.float32)  # 2 s, loud enough
    enroll.utterance(speech, 2.0)
    await enroll.transcribed(TranscriptionFrame("what's the weather like", "", ""))
    ok(voice.kept == [], "another sentence is heard but not kept")
    ok((speaker.LIVE / "en" / "old.wav").exists(), "the recordings already there stay until the new ones are complete")
    enroll.utterance(speech[:4000], 0.25)  # a cough: Whisper gives no text for it
    enroll.utterance(speech, 4.0)
    await enroll.transcribed(TranscriptionFrame(phrases("en")[0].lower(), "", ""))
    ok(voice.kept == [len(speech)], f"a phrase after an utterance with no text keeps the phrase's audio, not the cough ({voice.kept})")
    voice.kept.clear()
    for line in ("bliss oven to prodigy", "lease hope in her jacket", "plea zoe pen rocket"):
        enroll.utterance(speech, 4.0)
        await enroll.transcribed(TranscriptionFrame(line, "", ""))
    ok(len(voice.kept) == 3 and enroll.tracker.state()["current"] == 2, f"a phrase Whisper mangles is accepted after three tries, their audio kept ({len(voice.kept)})")
    for line in phrases("en"):
        enroll.utterance(speech, 4.0)
        await enroll.transcribed(TranscriptionFrame(line.lower(), "", ""))
    state = json.loads(first_run.STATE.read_text())
    ok(len(voice.kept) >= 6, f"each recognized phrase's audio is kept ({len(voice.kept)} utterances)")
    ok(voice.finished and done == [True] and state["phase"] == "voice-done", f"once all are read, the voice print is made and the window told ({state['phase']})")
    ok(enroll.tracker is None, "and enrollment is over")
    new = sorted(f.name for f in (speaker.LIVE / "en").glob("*.wav"))
    ok("old.wav" not in new and len(new) == len(voice.kept), f"the new recordings replace the old only now ({len(new)} files)")
    ok(not speaker.SKIPPED.exists(), "a learned voice clears an earlier Skip")

    (speaker.LIVE / "he").mkdir()
    (speaker.LIVE / "he" / "old.wav").write_bytes(b"")
    enroll.start("he")
    enroll.utterance(speech, 4.0)
    await enroll.transcribed(TranscriptionFrame(phrases("he")[0], "", ""))
    enroll.stop()
    ok(enroll.tracker is None and not speaker.CAPTURE.exists(), "skip stops it without a voice print")
    ok([f.name for f in (speaker.LIVE / "he").glob("*.wav")] == ["old.wav"] and not list(speaker.STAGED.rglob("*.wav")),
       "and what was there is kept, the partial recordings dropped")


async def test_window_and_silence(tmp: Path):
    speaker.CAPTURE, speaker.LIVE, speaker.STAGED = tmp / "capture", tmp / "live", tmp / "live-new"
    first_run.STATE = tmp / "first-run.json"
    speaker.EVERY, speaker.WINDOW_SECS, speaker.TRANSCRIBED_SECS = 0.05, 0.3, 0.1
    gone, is_open = [], [True]
    enroll = EnrollPhrases(FakeVoice(), Verdict(), FakeCheck(), window_open=lambda: is_open[0], on_gone=lambda: gone.append(True))
    enroll.start("en", again=True)
    ok(json.loads(first_run.STATE.read_text()).get("again") is True and not first_run.unfinished(),
       "outside a first run the state says so: a restart then isn't taken for a first run left unfinished")
    for _ in range(3):
        enroll.utterance(np.zeros(32000, dtype=np.float32) + 0.2, 2.0)  # Whisper never gives text for these
    await asyncio.sleep(0.5)
    ok(enroll.tracker and enroll.tracker.state()["phrases"][0]["done"], "utterances Whisper had no text for still count as tries")
    is_open[0] = False
    await asyncio.sleep(0.3)
    ok(gone == [True] and enroll.tracker is None and not speaker.CAPTURE.exists(), "the window gone, learning stops: voice isn't left answering nobody")
    enroll = EnrollPhrases(FakeVoice(), Verdict(), FakeCheck(), window_open=lambda: False, on_gone=lambda: gone.append(True))
    enroll.start("en")
    await asyncio.sleep(0.6)
    ok(len(gone) == 2 and enroll.tracker is None, "and so does one whose window never opened")
    enroll.stop(again=True)
    state = json.loads(first_run.STATE.read_text())
    ok(state["phase"] == "voice-done" and state["learned"] is False, "Later or Skip tells the window, no voice print made")


def test_when_to_offer(tmp: Path):
    speaker.LIVE, speaker.VOICEPRINT, speaker.SKIPPED = tmp / "live", tmp / "voiceprint.npy", tmp / "voice-skipped"
    ok(speaker.should_offer("en"), "no voice print: voice offers to learn it at start (Later offers again)")
    speaker.SKIPPED.touch()
    ok(not speaker.should_offer("en"), "after Skip it doesn't")
    speaker.SKIPPED.unlink()
    speaker.VOICEPRINT.write_bytes(b"")
    (speaker.LIVE / "en").mkdir(parents=True)
    (speaker.LIVE / "en" / "1.wav").write_bytes(b"")
    ok(not speaker.should_offer("en") and speaker.should_offer("he"), "with a print, only a language with no recordings")
    speaker.SKIPPED.touch()
    ok(not speaker.should_offer("he"), "and not after Skip")


async def main():
    test_matches_tolerates_whisper()
    test_rejects_other_text()
    test_phrase_accepted_after_tries()
    test_other_text_is_no_try()
    test_short_words_said_apart()
    test_other_hebrew_is_no_try()
    test_mangled_phrase_still_accepted()
    test_extra_phrase_when_short()
    test_when_to_offer(Path(tempfile.mkdtemp()))
    test_phrases_every_language()
    await test_enroll_phrases_keeps_recognized(Path(tempfile.mkdtemp()))
    await test_window_and_silence(Path(tempfile.mkdtemp()))
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
