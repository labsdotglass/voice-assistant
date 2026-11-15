"""Mute (no models): while muted, the mic reaches the pipeline as silence and nothing is played; the orb's
Mute / Unmute lines reach MUTE; news from sessions waits instead of being told, and is told after unmuting,
led by a note that says voice was muted; with no news, unmuting says nothing; and words heard just before
the mute are never answered."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import io
import sys
from contextlib import redirect_stdout

from pipecat.frames.frames import OutputAudioRawFrame

import talker
from helper_transport import AudioHelper, HelperInput, HelperOutput
from mute import MUTE
from talker import FEED, GREETING_RELEASED, Talker

results = []


def ok(cond, msg):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + msg)


class FakeProc:
    """The helper's stderr, line by line, then gone."""

    def __init__(self, lines):
        self._lines = [l.encode() + b"\n" for l in lines]
        self.returncode = 0

    @property
    def stderr(self):
        async def gen():
            for l in self._lines:
                yield l
        return gen()

    async def wait(self):
        return 0


class FakeHelper:
    def __init__(self, chunks):
        self.chunks, self.played = list(chunks), []

    async def read_mic(self):
        if not self.chunks:
            raise asyncio.CancelledError
        return self.chunks.pop(0)

    async def play(self, audio):
        self.played.append(audio)


def quiet_talker():
    """A talker whose replies are recorded, not made: what it would say, and that it started."""
    t = Talker()
    t._started = True
    t.replies = []

    async def reply(text, one_sentence=False):
        t.replies.append(list(FEED.pending))

    async def stop():
        t.stopped = True
    t._reply = reply
    t._stop_reply = stop
    t.stopped = False
    t.create_task = lambda coro: asyncio.ensure_future(coro)
    return t


async def main():
    MUTE.on_change = None
    MUTE.set(False)

    # The orb's menu (or a click on the muted orb) reaches MUTE.
    helper = AudioHelper(24000)
    with redirect_stdout(io.StringIO()):
        await helper._log_stderr(FakeProc(["audio_helper: command: mute"]))
    ok(MUTE.on, "Mute in the orb's menu mutes")
    with redirect_stdout(io.StringIO()):
        await helper._log_stderr(FakeProc(["audio_helper: command: unmute"]))
    ok(not MUTE.on, "Unmute (or a click on the muted orb) unmutes")

    # The mic: silence while muted, as it is otherwise.
    for muted in (True, False):
        MUTE.set(muted)
        pushed = []
        inp = HelperInput.__new__(HelperInput)
        inp._helper = FakeHelper([b"\x01\x02" * 160])

        async def push(frame, pushed=pushed):
            pushed.append(frame.audio)
        inp.push_audio_frame = push
        try:
            await inp._read()
        except asyncio.CancelledError:
            pass
        heard = pushed and any(pushed[0])
        ok(heard != muted, f"{'muted: the mic reaches the pipeline as silence' if muted else 'unmuted: the mic reaches it as it is'}")

    # The speaker: nothing played while muted.
    for muted in (True, False):
        MUTE.set(muted)
        out = HelperOutput.__new__(HelperOutput)
        out._helper, out._play_until = FakeHelper([]), 0.0
        await out.write_audio_frame(OutputAudioRawFrame(audio=b"\x00\x01" * 100, sample_rate=24000, num_channels=1))
        ok(bool(out._helper.played) != muted, "muted: nothing is played" if muted else "unmuted: it plays")

    # News while muted waits, and is told after unmuting, led by the muted note.
    GREETING_RELEASED.set()
    FEED.pending.clear()
    t = quiet_talker()
    MUTE.on_change = t.on_mute
    MUTE.set(True)
    await asyncio.sleep(0)
    ok(t.stopped, "muting stops what voice is saying")
    t.on_session_event({"type": "finished_elsewhere", "id": "s2", "title": "Fix login", "answer": "Done, tests pass."})
    await asyncio.sleep(0)
    ok(not t.replies and FEED.pending, "news while muted waits in the feed, unsaid")
    MUTE.set(False)
    await asyncio.sleep(0)
    told = t.replies[0] if t.replies else []
    ok(told and told[0].startswith("[a note from voice mode") and "muted" in told[0] and len(told) >= 2,
       "unmuted: the news is told, after a note that voice was muted")

    # No news: unmuting says nothing.
    FEED.pending.clear()
    t = quiet_talker()
    MUTE.on_change = t.on_mute
    MUTE.set(True)
    MUTE.set(False)
    await asyncio.sleep(0)
    ok(not t.replies, "unmuted with no news: nothing is said")

    # The note describes, quoting no phrase to copy.
    note = talker.unmuted_note(3)
    ok('"' not in note and "3 minutes" in note, "the note says how long, quoting no phrase")

    MUTE.on_change = None
    MUTE.set(False)


asyncio.run(main())
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
