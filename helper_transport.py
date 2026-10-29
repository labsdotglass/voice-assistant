"""Pipecat transport over bin/audio_helper: mic and speakers through macOS voice processing.

The helper removes our own TTS from the mic (echo cancellation), so the mic can stay open
while the assistant talks and the user can barge in on speakers.
"""

import re
import asyncio
import json
import os
import plistlib
import signal
import shutil
import struct
import time
from pathlib import Path

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
    TranscriptionFrame,
)
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.whisper.stt import WhisperSTTServiceMLX
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import TurnAnalyzerUserTurnStopStrategy
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.types import assert_given, require_given

from talker import emit

HELPER = Path(__file__).parent / "bin" / "audio_helper"
HELPER_SOURCE = Path(__file__).parent / "audio_helper" / "main.swift"
# The helper runs through Voice Assistant.app, so macOS lists its microphone permission under that name.
APP = Path(__file__).parent / "bin" / "Voice Assistant.app"
LAUNCHER = APP / "Contents" / "MacOS" / "voice-assistant"
LAUNCHER_SOURCE = Path(__file__).parent / "audio_helper" / "launcher.swift"
APP_ICON = Path(__file__).parent / "audio_helper" / "VoiceAssistant.icns"  # drawn by audio_helper/make_icon.swift
APP_INFO = {
    "CFBundleName": "Voice Assistant",
    "CFBundleDisplayName": "Voice Assistant",
    # A new id is a new app to macOS: it asks once more for the microphone (and Accessibility). build_app sees
    # the Info.plist differ and rebuilds the app on the next start.
    "CFBundleIdentifier": "glass.labs.voice-assistant",
    "CFBundleExecutable": "voice-assistant",
    "CFBundleIconFile": "VoiceAssistant",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "1.0",
    "CFBundleVersion": "1",
    "LSUIElement": True,
    "NSMicrophoneUsageDescription": "Voice Assistant listens so you can talk to Claude Code.",
}


async def build_helper():
    """Builds the audio helper on first start, or after its source changed (it isn't in git)."""
    if HELPER.exists() and HELPER.stat().st_mtime >= HELPER_SOURCE.stat().st_mtime:
        return
    logger.info("[lifecycle] building the audio helper (first start, or its source changed)")
    HELPER.parent.mkdir(exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "xcrun", "swiftc", "-O", str(HELPER_SOURCE), "-o", str(HELPER),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(
            "couldn't build the audio helper; it needs Xcode's command line tools (xcode-select --install): "
            + err.decode(errors="replace")[-400:]
        )
    logger.info("[lifecycle] audio helper built")


async def build_app():
    """Builds Voice Assistant.app, the helper's launcher, on first start or after its source changed.
    It's rebuilt rarely on purpose: macOS ties the microphone permission to this exact build."""
    info, resources = APP / "Contents" / "Info.plist", APP / "Contents" / "Resources"
    built = LAUNCHER.stat().st_mtime if LAUNCHER.exists() else 0
    if (built >= max(LAUNCHER_SOURCE.stat().st_mtime, APP_ICON.stat().st_mtime)
            and info.exists() and plistlib.loads(info.read_bytes()) == APP_INFO):
        return
    logger.info("[lifecycle] building Voice Assistant.app")
    LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    resources.mkdir(exist_ok=True)
    info.write_bytes(plistlib.dumps(APP_INFO))
    shutil.copyfile(APP_ICON, resources / APP_ICON.name)
    for cmd in (["xcrun", "swiftc", "-O", str(LAUNCHER_SOURCE), "-o", str(LAUNCHER)],
                ["codesign", "--force", "--sign", "-", str(APP)]):  # ad hoc: the bundle needs a signature
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode:
            raise RuntimeError(f"couldn't build Voice Assistant.app ({cmd[0]}): " + err.decode(errors="replace")[-400:])
    logger.info("[lifecycle] Voice Assistant.app built")
ORB_PAGE = Path(__file__).parent / "orb-page" / "dist" / "orb.html"
MIC_RATE = 16000
MIC_CHUNK = MIC_RATE // 50 * 2  # 20 ms of int16 mono
LEAD_SECS = 0.15  # how far playback may run ahead of real time; bounds what a flush has to drop
DEVICES_CHANGED = 3  # the helper's exit code when the mic or speakers changed: start a new one at once


# A restart in place (the daemon re-executes itself) hands on what must outlive it, in this variable:
# the audio helper (its pid and pipes: the orb stays on screen), the inbox's listening socket (sessions
# that ask meanwhile wait instead of finding no daemon) and the sessions registered with it.
HANDOFF_ENV = "VOICE_HANDOFF"
HANDOFF: dict = json.loads(os.environ.pop(HANDOFF_ENV, "") or "{}")


class AdoptedHelper:
    """An audio helper this process didn't start: the one the daemon ran before it re-executed itself.

    The same process (exec keeps the pid), so it's still our child: waitpid works. It offers what AudioHelper
    uses of an asyncio Process: stdin, stdout, stderr, pid, returncode, wait() and kill()."""

    def __init__(self, pid: int, stdin, stdout, stderr, fds: dict):
        self.pid, self.stdin, self.stdout, self.stderr, self.fds = pid, stdin, stdout, stderr, fds
        self.returncode: int | None = None

    @classmethod
    async def adopt(cls, handed: dict) -> "AdoptedHelper":
        loop = asyncio.get_running_loop()

        async def reader(fd: int) -> asyncio.StreamReader:
            stream = asyncio.StreamReader()
            await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stream), os.fdopen(fd, "rb", 0))
            return stream

        transport, protocol = await loop.connect_write_pipe(
            lambda: asyncio.StreamReaderProtocol(asyncio.StreamReader()), os.fdopen(handed["stdin"], "wb", 0))
        stdin = asyncio.StreamWriter(transport, protocol, None, loop)
        return cls(handed["pid"], stdin, await reader(handed["stdout"]), await reader(handed["stderr"]),
                   {k: handed[k] for k in ("stdin", "stdout", "stderr")})

    async def wait(self) -> int:
        if self.returncode is None:
            _, status = await asyncio.to_thread(os.waitpid, self.pid, 0)
            self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def kill(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _alive_child(pid: int) -> bool:
    """Whether `pid` is a helper still running as our child (one that died during the restart isn't)."""
    try:
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return False


class AudioHelper:
    def __init__(self, out_rate: int):
        self.out_rate = out_rate
        self.proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._orb = {"voice": "starting", "busy": False, "opening": False, "hidden": False}
        self._panel_visible = True
        self._settings: dict | None = None  # the orb menu's settings, once set_settings is called
        self._tasks: dict | None = None  # the last task snapshot, resent to a restarted helper
        # A helper being replaced for new devices, and how that went (True: the new one is up).
        self._replacing_for: asyncio.subprocess.Process | None = None
        self._replacing: asyncio.Future | None = None
        self._pressed: asyncio.Future | None = None  # press()'s answer from the helper
        self.on_setting = None  # (key, value) when you pick a setting from the orb's menu; ("more", item) for More's
        self._has_orb = os.environ.get("VOICE_ORB", "1") == "1" and ORB_PAGE.exists()

    async def ensure_started(self):
        async with self._lock:
            if self.proc:
                return
            handed = HANDOFF.pop("helper", None)
            if handed and _alive_child(handed["pid"]):
                # After a restart in place: the helper, and the orb, never went away. The orb keeps its
                # spinner until voice is ready again (the daemon clears it).
                self.proc = await AdoptedHelper.adopt(handed)
                self._orb.update(voice="starting", opening=True)
                logger.info(f"[lifecycle] audio helper kept through the restart (pid {self.proc.pid}): the orb stayed")
                asyncio.create_task(self._log_stderr(self.proc))
                self._send_orb()
                self._send(b"V", b"1" if self._panel_visible else b"0")
                return
            await build_helper()
            await build_app()
            self.proc = await asyncio.create_subprocess_exec(
                str(LAUNCHER), str(HELPER), "--out-rate", str(self.out_rate), *self.orb_args(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            logger.info(f"[lifecycle] audio helper started (pid {self.proc.pid}), orb {'on' if self._has_orb else 'off'}")
            line = (await self.proc.stderr.readline()).decode().strip()
            logger.info(line)
            if "ready" not in line:
                logger.error(f"[lifecycle] audio helper failed to start: {line!r}")
                if self.proc.returncode is None:
                    self.proc.kill()
                self.proc = None  # so a later start tries again rather than finding a dead helper
                raise RuntimeError(f"audio helper failed: {line}")
            asyncio.create_task(self._log_stderr(self.proc))
            self._send_orb()
            self._send(b"V", b"1" if self._panel_visible else b"0")
            if self._settings:
                self._send(b"C", json.dumps(self._settings).encode())
            if self._tasks is not None:
                self._send(b"T", json.dumps(self._tasks).encode())

    def orb_args(self) -> list[str]:
        """The helper's orb arguments: hidden from the start when it is now, so its window never flashes up."""
        if not self._has_orb:
            return []
        return ["--orb", str(ORB_PAGE)] + (["--orb-hidden"] if self._orb["hidden"] else [])

    def _replacement(self, proc) -> asyncio.Future:
        """How replacing this helper for new devices goes: True once the new one is up, False if it failed."""
        if self._replacing_for is not proc:
            self._replacing_for, self._replacing = proc, asyncio.get_running_loop().create_future()
        return self._replacing

    async def _log_stderr(self, proc):
        async for line in proc.stderr:
            text = line.decode().strip()
            if text == "audio_helper: command: stop":  # "Stop voice" from the orb's right-click menu
                logger.info("[lifecycle] stop requested from the orb's menu")
                emit({"type": "stop"})
            elif text == "audio_helper: command: clear":  # "Clear conversation" from the orb's right-click menu
                logger.info("[lifecycle] orb menu: clear the transcript")
                emit({"type": "clear"})
            elif text.startswith(("audio_helper: command: language ", "audio_helper: command: voice ",
                                  "audio_helper: command: pass-on ", "audio_helper: command: more ")):
                key, value = text.removeprefix("audio_helper: command: ").split(" ", 1)  # a setting from the orb's menu
                logger.info(f"[lifecycle] orb menu: {key} {value}")
                if self.on_setting:
                    self.on_setting(key.replace("-", "_"), value)
            elif text in ("audio_helper: command: show-panel", "audio_helper: command: hide-panel"):
                logger.info(f"[lifecycle] orb menu: {text.rsplit(' ', 1)[-1]}")
                emit({"type": "panel", "show": text.endswith("show-panel")})
            elif text.startswith("audio_helper: shortcut: "):  # how a press() went
                logger.info(f"[lifecycle] {text.removeprefix('audio_helper: ')}")
                if self._pressed and not self._pressed.done():
                    self._pressed.set_result(text.rsplit(" ", 1)[-1])
            elif text.startswith("audio_helper: orb dropped"):  # where you let go of the orb, and whether it snapped
                logger.info(text)
            else:
                logger.warning(text)
        # stderr closed: the helper, and with it the orb, is gone.
        code = await proc.wait()
        if code == DEVICES_CHANGED and self.proc is proc:
            # Earphones or other devices came or went and a new audio engine wouldn't start in place: a
            # new helper picks them up (the orb's window reopens where you left it: it saves its frame).
            logger.info("[lifecycle] audio helper restarting for the new mic or speakers")
            replaced = self._replacement(proc)
            self.proc = None
            try:
                await self.ensure_started()
            except Exception as e:
                logger.error(f"[lifecycle] audio helper couldn't restart ({e}): the orb is gone")
                replaced.done() or replaced.set_result(False)
                return
            replaced.done() or replaced.set_result(True)
            return
        logger.warning(f"[lifecycle] audio helper exited (code {code}): the orb is gone")

    async def read_mic(self) -> bytes:
        while True:
            proc = self.proc
            try:
                return await proc.stdout.readexactly(MIC_CHUNK)
            except asyncio.IncompleteReadError:
                if await proc.wait() != DEVICES_CHANGED:
                    raise
                # The helper is being replaced for new devices: read from the new one once it's up,
                # however long building it takes (a stale helper or app is rebuilt first).
                if not await asyncio.wait_for(asyncio.shield(self._replacement(proc)), 180):
                    raise

    def _send(self, kind: bytes, payload: bytes = b""):
        if self.proc and self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.write(kind + struct.pack("<I", len(payload)) + payload)

    async def play(self, pcm: bytes):
        proc = self.proc  # None while the helper is being replaced for new devices: the audio is dropped
        self._send(b"A", pcm)
        if proc and proc.stdin and not proc.stdin.is_closing():
            try:
                await proc.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass  # the helper went away mid-write; the next one gets what follows

    def flush(self):
        self._send(b"F")

    def set_panel_visible(self, visible: bool):
        """Tell the orb's right-click menu whether to offer Show or Hide transcript."""
        self._panel_visible = visible
        self._send(b"V", b"1" if visible else b"0")

    def set_settings(self, menu: dict):
        """The orb menu's settings (Voice, Pass on my words): the choices and which are ticked."""
        self._settings = menu
        self._send(b"C", json.dumps(menu).encode())

    async def press(self, shortcut: str) -> str:
        """Press a Claude app shortcut ("new_session", "next_session"): -> "done", "no-access", "no-app",
        "not-front", or "no-answer" if the helper didn't say."""
        self._pressed = asyncio.get_running_loop().create_future()
        self._send(b"K", shortcut.encode())
        try:
            return await asyncio.wait_for(self._pressed, 5)
        except asyncio.TimeoutError:
            return "no-answer"

    def tick(self):
        """A soft tick through voice's own audio (the tour moved by voice): not ducked, and echo-cancelled."""
        self._send(b"P")

    def drop_orb(self, x: float, y: float):
        """Carry the orb to a spot and let go, as a drag would: x, y place its centre, as fractions
        of the screen (0,0 bottom left). For trying out the edge snapping."""
        self._send(b"M", json.dumps({"x": x, "y": y}).encode())

    def set_tasks(self, snapshot: dict):
        """Your queued tasks, for the orb's count badge and task panel."""
        self._tasks = snapshot
        self._send(b"T", json.dumps(snapshot).encode())

    def set_orb(self, **state):
        """Update the floating orb: voice= listening/hearing/thinking/speaking..., busy= session working,
        opening= a session voice started or went to is opening (a spinner in the middle), hidden= out of sight
        while the onboarding window shows its own orb (first_run.orb_hidden)."""
        if {k: v for k, v in self._orb.items() if k in state} != state:
            self._orb.update(state)
            self._send_orb()

    def hand_off(self) -> dict | None:
        """The running helper's pid and pipe fds, made to survive exec: for the daemon about to re-execute."""
        proc = self.proc
        if not proc or proc.returncode is not None:
            return None
        if isinstance(proc, AdoptedHelper):
            fds = dict(proc.fds)
        else:
            fds = {"stdin": proc.stdin.transport.get_extra_info("pipe").fileno(),
                   "stdout": proc._transport.get_pipe_transport(1).get_extra_info("pipe").fileno(),
                   "stderr": proc._transport.get_pipe_transport(2).get_extra_info("pipe").fileno()}
        for fd in fds.values():
            os.set_inheritable(fd, True)
        return {"pid": proc.pid, **fds}

    def _send_orb(self):
        self._send(b"S", json.dumps(self._orb).encode())

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            self.proc.stdin.close()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()


class HelperInput(BaseInputTransport):
    def __init__(self, helper: AudioHelper, params: TransportParams):
        super().__init__(params)
        self._helper = helper
        self._reader: asyncio.Task | None = None

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._helper.ensure_started()
        self._reader = self.create_task(self._read())
        await self.set_transport_ready(frame)

    async def _read(self):
        while True:
            chunk = await self._helper.read_mic()
            await self.push_audio_frame(InputAudioRawFrame(audio=chunk, sample_rate=MIC_RATE, num_channels=1))

    async def cleanup(self):
        if self._reader:
            await self.cancel_task(self._reader)
        await super().cleanup()
        await self._helper.stop()


class HelperOutput(BaseOutputTransport):
    def __init__(self, helper: AudioHelper, params: TransportParams):
        super().__init__(params)
        self._helper = helper
        self._play_until = 0.0

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._helper.ensure_started()
        await self.set_transport_ready(frame)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, InterruptionFrame):
            self._helper.flush()  # drop what the speaker still has queued, right now
            self._play_until = 0.0
        await super().process_frame(frame, direction)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        await self._helper.play(frame.audio)
        # Pace writes to real time so an interruption only has a little audio to drop.
        now = time.monotonic()
        self._play_until = max(self._play_until, now) + len(frame.audio) / 2 / frame.sample_rate
        ahead = self._play_until - now
        if ahead > LEAD_SECS:
            await asyncio.sleep(ahead - LEAD_SECS)
        return True


class HelperAudioTransport(BaseTransport):
    def __init__(self, out_rate: int = 24000):
        super().__init__()
        params = TransportParams(
            audio_in_enabled=True, audio_in_sample_rate=MIC_RATE,
            audio_out_enabled=True, audio_out_sample_rate=out_rate,
        )
        self.helper = self._helper = AudioHelper(out_rate)
        self._input = HelperInput(self._helper, params)
        self._output = HelperOutput(self._helper, params)

    def input(self) -> HelperInput:
        return self._input

    def output(self) -> HelperOutput:
        return self._output


class QuestionEndsTurn(TurnAnalyzerUserTurnStopStrategy):
    """Smart-turn's stop strategy, except a question Whisper heard ends the turn even when smart-turn didn't.

    Smart-turn judges some finished questions unfinished by their sound ("What time zone is Tokyo in?" ends
    like a sentence that goes on) and then waits out its 3 s of silence. Whisper punctuates from the whole
    sentence, and a pause mid-thought almost never comes out as a question, so a final "?" settles it.
    Anything else keeps smart-turn's wait, so pausing mid-sentence still doesn't get you answered.
    """

    def __init__(self, **kwargs):
        super().__init__(turn_analyzer=LocalSmartTurnAnalyzerV3(), **kwargs)

    async def _handle_transcription(self, frame: TranscriptionFrame):
        if self._vad_stopped and not self._vad_user_speaking and not self._turn_complete and frame.text.rstrip().endswith("?"):
            logger.info(f"[turn] a question, though smart-turn heard it unfinished: ending the turn ({frame.text.strip()!r})")
            self._turn_complete = True
            self._turn_analyzer.clear()  # or its own 3 s timer ends the next turn early
        await super()._handle_transcription(frame)


class BargeInGate(FrameProcessor):
    """Makes the VAD stricter while the assistant speaks.

    Echo cancellation leaves a quiet residue of our own voice that Silero still scores as
    speech (measured: smoothed volume up to ~0.74). While the bot talks, only louder, more
    confident speech counts as the user, so the assistant doesn't interrupt itself.
    """

    def __init__(self, vad: VADAnalyzer, **kwargs):
        super().__init__(**kwargs)
        self._vad = vad
        self._normal = vad.params
        self._strict = VADParams(confidence=0.85, start_secs=0.3, stop_secs=self._normal.stop_secs, min_volume=0.8)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._vad.set_params(self._strict)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._vad.set_params(self._normal)
        await self.push_frame(frame, direction)


# What Whisper tends to invent from silence or leftover echo.
HALLUCINATIONS = {
    "thank you", "thank you thank you", "thanks", "thanks for watching", "thank you for watching",
    "you", "bye", "bye bye", "okay", "so", "um", "uh", "hmm", "please subscribe",
    "תודה", "תודה רבה", "תודה רבה לכם", "תודה שצפיתם",  # Hebrew Whisper's
}


def collapse_runs(text: str) -> str:
    """A letter Whisper got stuck repeating (a long "uhhh", "אהההה...") cut to three: it can run on for lines."""
    return re.sub(r"([^\W\d_])\1{3,}", r"\1\1\1", text)


def kept_text(segments: list[dict], no_speech_cutoff: float, you: bool) -> str:
    """Whisper's text for one utterance, without the segments it rates as likely silence (pipecat's rule:
    no_speech_prob at or above the cutoff, or its tell-tale compression ratio). When the voice check matched
    the utterance to you it isn't silence: a lone "yes" in 0.7 s is often rated that way, so it's kept."""
    text = []
    for s in segments:
        words = s.get("text", "").strip()
        silent = s.get("compression_ratio") == 0.5555555555555556 or s.get("no_speech_prob", 0.0) >= no_speech_cutoff
        if words and silent:
            how = "kept: the voice check matched you" if you else "dropped as likely silence"
            logger.info(f"[whisper] {how}: {words!r} (no_speech {s.get('no_speech_prob', 0.0):.2f})")
        if words and (you or not silent):
            text.append(words)
    return collapse_runs(" ".join(text))


class WhisperKeepingYou(WhisperSTTServiceMLX):
    """Pipecat's MLX Whisper, keeping what it would drop as silence when the voice check matched you (kept_text)."""

    def __init__(self, verdict, **kwargs):
        super().__init__(**kwargs)
        self._verdict = verdict  # speaker.Verdict: in long before Whisper's ~300 ms

    async def run_stt(self, audio: bytes):
        try:
            import mlx_whisper

            await self.start_processing_metrics()
            language = assert_given(self._settings.language)
            chunk = await asyncio.to_thread(
                mlx_whisper.transcribe,
                np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0,
                path_or_hf_repo=require_given(self._settings.model, "Whisper model"),
                temperature=assert_given(self._settings.temperature),
                language=language,
            )
            await self.stop_processing_metrics()
            # The next utterance's start clears the verdict: then this one counts as unmatched, as before.
            you = self._verdict is not None and self._verdict.ready.is_set() and self._verdict.matched
            text = kept_text(chunk.get("segments", []), assert_given(self._settings.no_speech_prob), you)
            if text:
                await self._handle_transcription(text, True, language)
                yield TranscriptionFrame(f" {text} ", self._user_id, time_now_iso8601(), language)
        except Exception as e:
            yield ErrorFrame(error=f"Unknown error occurred: {e}")


class HallucinationFilter(FrameProcessor):
    """Drops those words when nothing shows you said them. After SpeakerGate: an utterance the voice check
    matched to your voice is real speech, not silence or echo, so "okay" or "thanks" from you gets through."""

    def __init__(self, verdict=None, **kwargs):
        super().__init__(**kwargs)
        self._verdict = verdict  # speaker.Verdict: the utterance's voice check, already in by now

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and not (self._verdict and self._verdict.matched):
            norm = "".join(c for c in frame.text.lower() if c.isalpha() or c == " ").split()
            if " ".join(norm) in HALLUCINATIONS:
                logger.info(f"(dropped likely hallucination: {frame.text!r})")
                return
        await self.push_frame(frame, direction)
