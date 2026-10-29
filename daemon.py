"""Voice daemon for the Claude desktop mod: open mic -> talker (Haiku) -> voice, plus a session-event inbox.

The mod spawns this and reads `@@VOICE {json}` lines from stdout (see talker.emit).
The mod POSTs session events as JSON to http://voice/event over the unix socket at --socket.
Each session running the mod POSTs /register and long-polls GET /commands; only the session you focused last
in the app (focus.py) gets commands, and only its events reach the talker.
Everything else (logs) goes to stderr.

    uv run daemon.py --socket ~/.voice-assistant/run/voice.sock [--no-aec] [--headphones] [--restart WHY]

Default audio goes through bin/audio_helper (macOS voice processing): echo-cancelled, so you
can talk over the assistant on speakers. --no-aec falls back to PyAudio, where the mic is
muted while the assistant speaks unless --headphones. PyAudio isn't installed by default (it builds against
PortAudio): brew install portaudio, then uv run --extra no-aec daemon.py --no-aec.
"""

import argparse
import asyncio
import json
import os
import signal
import socket
import sys
import threading
from pathlib import Path

from aiohttp import web
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame, TTSUpdateSettingsFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX
from pipecat.transcriptions.language import Language
from pipecat.turns.user_mute import AlwaysUserMuteStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

import first_run
import new_session
import home
import settings
from focus import FocusWatcher
from interim import InterimTranscriber, SaidSoFar, load_speech_models, set_draft_language
from hebrew_voice import KokoroAnyLanguage, load_fast_hebrew, load_fast_kokoro
from speaker import CAPTURE, SKIPPED, VOICEPRINT, EnrollPhrases, should_offer, SpeakerCheck, SpeakerGate, Verdict, VoicePrint, load_encoder
from helper_transport import HANDOFF, HANDOFF_ENV, BargeInGate, HallucinationFilter, HelperAudioTransport, QuestionEndsTurn, WhisperKeepingYou
from talker import ENDING, ENROLLING, FEED, FIRST_RUN, FIRST_TOUR, GREETING_RELEASED, RESTART, VOICE_STATE, mark_restart, restart_reason, MENU, TOUR_OPEN, tour_is_open, NEW_SESSION, OUTBOX, PROGRESS_OFF, SPEECH_READY, TASKS, Talker, VoiceStateReporter, emit, set_pass_on


OWNER_FILE = home.RUN / "owner"
INBOX: dict = {}  # the inbox's listening socket ("socket"), handed on by a restart in place
SCRIPT = sys.argv[0]  # this file, as it was started: re-executed the same way, so `pkill -f "python3 daemon.py"` still finds it


async def until_quiet(is_speaking, most: float = 8.0, step: float = 0.2) -> float:
    """Waits while voice is replying or speaking, `most` seconds at most: -> how long it waited."""
    waited = 0.0
    while waited < most and is_speaking():
        await asyncio.sleep(step)
        waited += step
    return waited
# Another session's finished turn is announced when it took at least this long: 0, every one.
# (A candidate for settings.py, if a quieter choice is ever wanted in the orb's menu.)
ANNOUNCE_MIN_SECS = 0
SUMMARY_WAIT = 6  # seconds to wait for the app's summary of that turn before going by its answer


async def serve_events(talker: Talker, socket_path: str, on_event=None, on_panel=None, on_stop=None, on_orb_drop=None, on_enroll=None) -> web.AppRunner:
    busy: dict[str, bool] = {}  # per session, from its turn events
    waits = new_session.Waits()  # other sessions waiting for you: told once each

    def on_focus(session: str, title: str, project: str):
        # At startup, and each time you switch sessions in the app: voice talks to that one now,
        # and the talker hears its name and ID.
        initial = not OUTBOX.target
        talker.on_session_event(
            {
                "type": "switched",
                "title": title,
                "project": project,
                "session": session,
                "busy": busy.get(session, False),
                "initial": initial,
            }
        )
        if initial:  # voice just started: what finished elsewhere while it was off (the mod's notes)
            done = new_session.finished_elsewhere(session)
            if done:
                logger.info(f"[finished elsewhere] {len(done)} while voice was off: {[d['title'] for d in done]}")
                talker.on_session_event({"type": "finished_while_off", "sessions": done[:5]})
            waiting = new_session.waiting_elsewhere(session)
            if waiting:
                logger.info(f"[waiting elsewhere] {len(waiting)} waiting since voice was off: {[w['title'] for w in waiting]}")
                talker.on_session_event({"type": "waiting_while_off", "sessions": waiting[:5]})
        OUTBOX.focus(session, title or f"a session in {project}")
        OWNER_FILE.write_text(session)  # after a reload, only this session brings voice back
        if on_event:
            on_event()

    async def register(request: web.Request):
        # A session's mod (`client` is one load of it: a reload replaces its old self).
        ev = await request.json()
        OUTBOX.register(ev["client"], ev["session"])
        busy[ev["session"]] = bool(ev.get("busy"))
        if ev["session"] == OUTBOX.target:
            # The session voice talks to reports whether it's working. At startup voice met it before it
            # registered, so it took it as idle: a session mid-turn read as idle until its next turn event.
            talker.on_session_event({"type": "busy_now", "busy": busy[ev["session"]]})
            if on_event:
                on_event()  # the orb's working dots
        logger.info(f"session {ev['session'][:8]} registered" + (" (focused)" if ev["session"] == OUTBOX.target else ""))
        return web.json_response({"ok": True})

    async def commands(request: web.Request):
        out = await OUTBOX.take(request.query["client"], wait=float(request.query.get("wait", 15)))
        return web.json_response(
            {
                "commands": out,
                "owner": out is not None,
                "title": OUTBOX.title,  # the session voice talks to, for the Voice panel
                "reachable": OUTBOX.owner is not None,
                "stopping": OUTBOX.stopping,
            }
        )

    async def enroll(request: web.Request):
        # /voice-enroll: learn the user's voice again from the phrases in the window the mod opens.
        if on_enroll:
            on_enroll()
        return web.json_response({"ok": True})

    async def orb_drop(request: web.Request):
        # Drop the orb at a spot, as a drag would, to try out the edge snapping (orb_demo.py).
        spot = await request.json()
        if on_orb_drop:
            on_orb_drop(float(spot["x"]), float(spot["y"]))
        return web.json_response({"ok": bool(on_orb_drop)})

    async def stop(request: web.Request):
        logger.info("[lifecycle] stop requested over the socket (Stop voice in a session)")
        OUTBOX.stop()  # tells every session's poll, so none of them restarts us
        if on_stop:
            asyncio.get_running_loop().call_later(0.3, on_stop)
        return web.json_response({"ok": True})

    async def event(request: web.Request):
        ev = await request.json()
        logger.debug(f"session event: {json.dumps(ev)[:300]}")
        if ev.get("type") == "panel" and ev.get("session") == OUTBOX.target:  # keeps the orb's menu label right
            logger.info(f"panel visible: {ev.get('visible')}")
            if on_panel:
                on_panel(bool(ev.get("visible")))
            return web.json_response({"ok": True})
        session = ev.get("session")
        if ev.get("type") in ("turn_start", "turn_complete") and session:
            busy[session] = ev["type"] == "turn_start"
            logger.info(f"[turn] {ev['type']} from {session[:8]}" + (" (aborted)" if ev.get("isAborted") else "")
                        + (" (the session voice talks to)" if session == OUTBOX.target else ""))
        # Any session answering or carrying on ends its wait, the one you're in included (you may have gone
        # there to answer it): told again if it asks again, and news of it not yet spoken is withdrawn.
        if ev.get("type") in ("turn_start", "turn_complete", "permission_done", "question_done") and session:
            if waits.moved_on(session) and session != OUTBOX.target:
                talker.on_session_event({"type": "wait_over", "session": session})
        if session != OUTBOX.target:
            # A permission or a question form. Not a closing question (it has buttons): it comes with the turn's
            # end, and announce_finished tells you about that turn and what it needs from you.
            if (ev.get("type") in ("permission", "question") and not ev.get("buttons") and session and OUTBOX.target
                    and waits.new(ev)):
                asyncio.create_task(announce_waiting(ev))
            if (ev.get("type") == "turn_complete" and OUTBOX.target and not ev.get("isAborted")
                    and ev.get("durationMs", 0) >= ANNOUNCE_MIN_SECS * 1000):
                asyncio.create_task(announce_finished(ev))
            if ev.get("type") == "progress":
                logger.info(f"[progress] not spoken: from session {str(session)[:8]}, not the one you're in")
            return web.json_response({"ok": True})  # another session's work: not what you're talking about
        talker.on_session_event(ev)
        if on_event:
            on_event()
        return web.json_response({"ok": True})

    async def announce_finished(ev: dict):
        # Another session finished a turn: the talker tells you which, with the app's summary of it once
        # the app has written it (a moment after the turn ends), and asks whether you want to go there.
        session = ev["session"]
        found = None
        await asyncio.sleep(1)  # the app's summary is never there at once, and the mod's note lands meanwhile
        for _ in range(SUMMARY_WAIT):
            found = await asyncio.to_thread(new_session.turn_summary, session)
            if not found or found["summary"]:
                break
            await asyncio.sleep(1)
        if not found or session == OUTBOX.target:  # not a session the app shows, or you've gone there meanwhile
            return
        logger.info(f"[finished elsewhere] {found['title']!r} ({session[:8]})" + ("" if found["summary"] else ", no summary yet"))
        (new_session.FINISHED_DIR / f"{session}.json").unlink(missing_ok=True)  # told now: not again when voice next starts
        talker.on_session_event({"type": "finished_elsewhere", "session": session, "title": found["title"],
                                 "project": found["project"], "summary": found["summary"], "answer": ev.get("answer", "")})

    async def announce_waiting(ev: dict):
        # Another session is waiting for you (a permission on screen or a question form): the talker tells you
        # which and what it wants, at the next quiet moment, and offers to take you there.
        session = ev["session"]
        found = await asyncio.to_thread(new_session.turn_summary, session)
        # Not a session the app shows, you've gone there meanwhile, or it was answered while we looked it up.
        if not found or session == OUTBOX.target or not waits.still(ev):
            return
        what = new_session.wait_described(ev)
        logger.info(f"[waiting elsewhere] {found['title']!r} ({session[:8]}) wants {what}")
        talker.on_session_event({"type": "waiting_elsewhere", "session": session, "title": found["title"],
                                 "project": found["project"], "what": what})

    app = web.Application()
    app.router.add_post("/event", event)
    app.router.add_post("/register", register)
    app.router.add_get("/commands", commands)
    app.router.add_post("/stop", stop)
    app.router.add_post("/enroll", enroll)
    app.router.add_post("/orb-drop", orb_drop)
    runner = web.AppRunner(app)
    await runner.setup()
    handed = HANDOFF.pop("socket", None)
    if handed is not None:  # after a restart in place: the same socket, so no session found it gone
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, fileno=handed)
    else:
        Path(socket_path).unlink(missing_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(socket_path)
    INBOX["socket"] = sock
    await web.SockSite(runner, sock).start()
    logger.info(f"event inbox on {socket_path}")
    watch = FocusWatcher(on_focus)
    watch.poll()  # know where you are before the first command goes out
    asyncio.get_running_loop().create_task(watch.run())
    return runner


def orb_menu() -> dict:
    """What the orb's menu shows: the settings with their choices, and More's Progress updates switch."""
    return {**settings.menu(settings.load()), "progress": not PROGRESS_OFF.exists()}


# Pipecat's language for each of the menu's languages.
LANGUAGE = {"en": Language.EN_US, "he": Language.HE, "es": Language.ES, "fr": Language.FR, "it": Language.IT,
            "pt": Language.PT_BR, "hi": Language.HI}


def voice_language(voice: str) -> Language:
    """The language a voice speaks; Kokoro's British voices (bf_, bm_) speak British English."""
    code = settings.voice_language(voice)
    return Language.EN_GB if code == "en" and voice.startswith("b") else LANGUAGE[code]


def hearing(code: str, default_model: str) -> tuple[str, Language]:
    """The final Whisper model and language for one of the menu's languages."""
    return settings.language(code)[4] or default_model, LANGUAGE[code]


async def main(socket_path: str, aec: bool, headphones: bool, restart: str | None = None):
    OUTBOX.clients.update(HANDOFF.pop("clients", {}))  # after a restart in place: the sessions registered before it
    if aec:
        transport = HelperAudioTransport(out_rate=24000)
    else:
        # Only here: PyAudio is an extra, so a normal install doesn't need PortAudio.
        try:
            from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
        except Exception as e:  # pipecat raises its own error when pyaudio is missing
            sys.exit(f"--no-aec needs PyAudio ({e}). Install PortAudio (brew install portaudio), then run "
                     "uv run --extra no-aec daemon.py --no-aec")
        transport = LocalAudioTransport(LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True))
    default_model = os.environ.get("WHISPER_MODEL", MLXModel.LARGE_V3.value)
    chosen = settings.load()  # your Language, Voice and Pass on my words choices from the orb's menu
    whisper_model, heard_in = hearing(chosen["language"], default_model)
    # A restart (the mod's --restart, or this daemon's own language switch) carries on the conversation.
    RESTART["why"] = restart_reason(restart)
    if RESTART["why"].startswith("the language is now"):
        RESTART["language"] = next((label for code, label, *_ in settings.LANGUAGES if code == chosen["language"]), "")
    if RESTART["why"]:
        logger.info(f"[lifecycle] this start is a restart: {RESTART['why']}")
    # First start with no voice print: the onboarding window is open (language, setup, the tour, your voice).
    # A daemon restarted meanwhile gets no VOICE_FIRST_TOUR from the mod, so the state file says it too.
    first_tour = not VOICEPRINT.exists() and (os.environ.get("VOICE_FIRST_TOUR") == "1" or first_run.unfinished())
    if first_tour and RESTART["why"]:
        RESTART["why"] = ""  # nothing to carry on: the first run's greeting hasn't been said yet
        logger.info("[lifecycle] restarted during the first run: it carries on, its greeting still to come")
    first_run.serve_requests()  # the window's requests (retry, done; learning your voice's), from here on
    set_draft_language(chosen["language"])
    # The greeting waits until the speech models are loaded (the orb stays gray): Hebrew's voice too, so
    # its first start, which downloads them (about 2 GB), never looks like voice ignoring you.
    SPEECH_READY.clear()
    also = [load_fast_hebrew] if chosen["voice"] == "he_shaul" else [load_fast_kokoro]
    if first_tour:
        # Setup's progress and readiness go to the window (first-run.json); a failure there offers Retry.
        # Everything is fetched here, the shared files too (tts and the voice print below load lazily), so
        # the daemon answers the mod and the window's requests at once, and stays up when a download fails.
        def setup():
            first_run.fetch_shared()  # Kokoro's ONNX files, whole or not at all
            load_encoder()  # ECAPA, for the voice check
            load_speech_models(whisper_model, also, strict=True)

        first_run.watch_setup(setup, chosen["language"])
        # The greeting waits for Start talking (or for the window to be gone once setup is over).
        GREETING_RELEASED.clear()
        end_first_run = first_run.hold_until_done(GREETING_RELEASED.set, window_open=tour_is_open)
    else:
        threading.Thread(target=load_speech_models, args=(whisper_model, also), daemon=True).start()
    verdict = Verdict()  # each utterance's voice check: the gate, Whisper and the filter all read it
    stt = WhisperKeepingYou(verdict, settings=WhisperSTTServiceMLX.Settings(model=whisper_model, language=heard_in))
    tts = KokoroAnyLanguage(settings=KokoroTTSService.Settings(voice=chosen["voice"], language=voice_language(chosen["voice"])), lazy=first_tour)
    talker = Talker()
    # The floating orb (helper transport only): the voice state, plus a ring while the session works.
    orb = transport.helper if aec else None
    # The orb stays gray until the greeting starts playing (with no greeting, until the models load),
    # and a soft tick says voice is ready as it turns colored.
    reporter = VoiceStateReporter(
        on_change=(lambda v: orb.set_orb(voice=v)) if orb else None,
        gray_until_greeting=os.environ.get("VOICE_GREETING", "1") != "0" or bool(RESTART["why"]),  # every restart says it's back
        on_ready=(lambda: (orb.tick(), orb.set_orb(opening=False))) if orb else None,  # a restart's spinner ends
    )

    # Only your voice gets answered, once enrolled; without a voice print, everyone is. Learning it runs by
    # phrases in the onboarding window: in a first run, once the window's Your voice step asks (enroll).
    FIRST_TOUR["on"] = first_tour  # the first run's own greeting
    in_first_run = lambda: first_tour and first_run.phase() != "done"
    # The onboarding window shows its own orb, so the floating one stays out of sight meanwhile: through a first
    # run (a restart mid-way too), and while learning your voice again has the window open. Decided before the
    # helper starts, so it starts hidden, then kept up to date; it comes back once the first run is over.
    again_window = first_run.AgainWindow(window_open=tour_is_open)
    orb_hidden = lambda: first_run.orb_hidden(first_tour, first_run.phase(), again_window.showing())

    async def keep_orb_hidden():
        while True:
            orb.set_orb(hidden=orb_hidden())
            await asyncio.sleep(0.25)

    if orb:
        orb.set_orb(hidden=orb_hidden())
        orb_watch = asyncio.create_task(keep_orb_hidden())  # held, so it isn't collected
    voice = VoicePrint(lazy=first_tour)
    if voice.upgrade():
        logger.info(f"[speaker] voice print rebuilt as {len(voice.print)} half-second pieces, from the enrollment recordings")

    def learn_voice(open_window: bool, tell: bool = True):
        """The enroll request, /voice-enroll, More > Learn my voice again, or a language you haven't enrolled in
        yet: the phrases, in the current language, replace that language's recordings in the voice print.
        open_window: the mod opens the onboarding window at Your voice (/voice-enroll opens it itself)."""
        again = not in_first_run()
        if again:
            again_window.opening()  # the window comes up at Your voice (the mod's, or /voice-enroll's own)
        started = phrases.start(settings.load()["language"], **({"again": True} if again else {}))
        ENROLLING["on"] = True
        if open_window:
            emit({"type": "enroll"})
        if started and again and tell:  # in a first run the window says it all; voice greets after it
            talker.on_session_event({"type": "enroll_again"})

    def enrolled():
        ENROLLING["on"] = False
        if not in_first_run():
            talker.on_session_event({"type": "enrolled"})

    def not_now(why: str):
        # The window's Later (offered again at the next start), Skip (not offered at start again; More > Learn my
        # voice again and /voice-enroll still do it), or the window gone. Either way the voice print, if any, stays.
        logger.info(f"[speaker] not learning the user's voice now: {why}")
        ENROLLING["on"] = False
        if why == "Skip":
            SKIPPED.touch()
        phrases.stop(quiet=why == "the window is gone", **({} if in_first_run() else {"again": True}))

    check = SpeakerCheck(voice, verdict, on_enrolled=enrolled)  # on_enrolled: enroll_voice.py --live, by seconds
    phrases = EnrollPhrases(voice, verdict, check, on_enrolled=enrolled, window_open=tour_is_open, on_gone=lambda: not_now("the window is gone"))
    check.phrases = phrases
    speaker = [check, phrases, SpeakerGate(verdict)]
    if first_tour:
        CAPTURE.unlink(missing_ok=True)  # a crashed run's capture: in a first run, it waits for the window
    elif os.environ.get("VOICE_ENROLL", "1") != "0" and (CAPTURE.exists() or should_offer(chosen["language"])):
        # No voice print yet (Later offers it again each start), or started in a language voice hasn't heard you in
        # (you just switched to it); not after Skip. The greeting asks for it.
        logger.info(f"[speaker] offering to learn the user's voice in {chosen['language']}")
        learn_voice(open_window=True, tell=False)
    # The window's requests (serve_requests' thread): handled on the event loop, as the pipeline is.
    loop = asyncio.get_running_loop()
    first_run.on_request("enroll", lambda: loop.call_soon_threadsafe(learn_voice, False))
    first_run.on_request("later", lambda: loop.call_soon_threadsafe(not_now, "Later"))
    first_run.on_request("skip", lambda: loop.call_soon_threadsafe(not_now, "Skip"))
    logger.info(
        f"[speaker] answering only the enrolled voice ({VOICEPRINT})" if VOICEPRINT.exists()
        else "[speaker] no voice print enrolled: answering every voice"
    )

    vad = SileroVADAnalyzer()
    aggregators = LLMContextAggregatorPair(
        LLMContext(),
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad,
            user_turn_strategies=UserTurnStrategies(stop=[QuestionEndsTurn()]),  # a question ends the turn
            # Without echo cancellation Whisper would hear the assistant, so mute the mic while it talks.
            user_mute_strategies=[] if aec or headphones else [AlwaysUserMuteStrategy()],
        ),
    )
    pipeline = Pipeline(
        [
            transport.input(),
            InterimTranscriber(),  # draft lines while you talk; the final pass replaces them
            *speaker[:1],  # SpeakerCheck: scores each utterance against your voice print as it ends
            stt,
            *speaker[1:],  # EnrollPhrases: while it learns your voice, keeps the phrases it recognizes; SpeakerGate: drops what another voice said
            HallucinationFilter(verdict),  # "okay" from nowhere; not when the voice check matched you
            SaidSoFar(),  # the draft bubble keeps each finished piece of your turn
            BargeInGate(vad),
            aggregators.user(),
            talker,
            reporter,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )
    task = PipelineTask(pipeline)
    # [lifecycle] lines: why voice starts and stops. Read them with
    #   tail -f ~/.voice-assistant/logs/voice.log ~/.voice-assistant/logs/mod.log | grep --line-buffered lifecycle
    close_reason = {"why": "the pipeline ended"}

    @task.event_handler("on_pipeline_started")
    async def _started(task, frame):
        logger.info("[lifecycle] voice pipeline started")

    @task.event_handler("on_pipeline_finished")
    async def _finished(task, frame):
        why = getattr(frame, "reason", None)
        logger.info(f"[lifecycle] voice pipeline finished: {type(frame).__name__}" + (f" ({why})" if why else ""))
        close_reason["why"] = f"{type(frame).__name__}" + (f": {why}" if why else "")

    @task.event_handler("on_pipeline_error")
    async def _error(task, frame):
        logger.error(f"[lifecycle] voice pipeline error: {getattr(frame, 'error', frame)} (fatal: {getattr(frame, 'fatal', '?')})")

    @task.event_handler("on_idle_timeout")
    async def _idle(task):
        logger.warning("[lifecycle] voice pipeline idle timeout: no activity for the idle window; Pipecat cancels the pipeline")
        close_reason["why"] = "idle timeout"

    for name in ("on_heartbeat_timeout", "on_pipeline_timeout", "on_setup_timeout"):
        task.add_event_handler(name, lambda *a, n=name: logger.warning(f"[lifecycle] voice pipeline {n[3:].replace('_', ' ')}"))
    inbox = await serve_events(
        talker,
        socket_path,
        on_event=(lambda: orb.set_orb(busy=FEED.busy)) if orb else None,
        on_panel=orb.set_panel_visible if orb else None,
        on_stop=lambda: asyncio.create_task(task.cancel()),  # Stop voice from a session that didn't start us
        on_orb_drop=orb.drop_orb if orb else None,
        on_enroll=lambda: learn_voice(open_window=False),  # /voice-enroll opens the window itself
    )

    def end_voice(why: str):
        # The talker's end_voice, once its goodbye has played: stopped exactly as Stop voice is.
        logger.info(f"[lifecycle] stop requested by the talker ({why})")
        OUTBOX.stop()  # tells every session's poll, so none of them restarts us
        asyncio.get_running_loop().call_later(0.3, lambda: asyncio.create_task(task.cancel()))

    ENDING.on_end = end_voice

    def on_setting(key: str, value: str):
        # Picked from the orb's menu: kept for next time and applied right away.
        if key == "more":  # More's items
            if value == "enroll":
                learn_voice(open_window=True)  # the mod opens the window at Your voice
            elif value == "tour":
                emit({"type": "tour"})  # the mod shows the slideshow
            elif value in ("progress", "progress_on", "progress_off"):  # the menu toggles; by voice it's said
                is_on = PROGRESS_OFF.exists() if value == "progress" else value == "progress_on"
                PROGRESS_OFF.unlink(missing_ok=True) if is_on else PROGRESS_OFF.touch()
                logger.info(f"[settings] progress updates {'on' if is_on else 'off'}")
            if orb:
                orb.set_settings(orb_menu())
            return
        if not settings.save(key, value):
            return
        if key == "language":
            # One language at a time, so a switch starts voice afresh in it: its Whisper, voice, talker
            # and drafts, its models loaded before the orb turns colored, and learning your voice first if
            # voice hasn't heard you in it yet. Ended as pkill ends it, the session starts it again at once.
            settings.save("voice", settings.voices(value)[0][0])
            asyncio.create_task(restart_when_quiet(f"the language is now {value}"))
        elif key == "voice":
            asyncio.create_task(task.queue_frames([
                TTSUpdateSettingsFrame(delta=KokoroTTSService.Settings(voice=value, language=voice_language(value))),
                TTSSpeakFrame(settings.VOICE_SAMPLE[settings.voice_language(value)]),  # so you hear the new voice at once
            ]))
        elif key == "pass_on":
            set_pass_on(value)
        if orb:
            orb.set_settings(orb_menu())

    async def restart_when_quiet(why: str):
        # The orb's menu picks a language at any moment: what voice is saying, an answer to what you just
        # asked included, finishes first (a few seconds at most), or it's lost with the daemon.
        waited = await until_quiet(lambda: talker._replying() or VOICE_STATE["now"] == "speaking")
        if waited:
            logger.info(f"[lifecycle] waited {waited:.1f}s for voice to finish speaking before restarting")
        restart_voice(why)

    def restart_voice(why: str, after: float = 0.3):
        logger.info(f"[lifecycle] restarting voice: {why}")
        mark_restart(why)  # so the next start knows it's this restart, and why
        asyncio.get_running_loop().call_later(after, restart_in_place, why)

    def restart_in_place(why: str):
        """A fresh daemon in this same process (exec), not a new one: the orb's helper, the inbox's
        socket and the sessions registered with it are handed on, so the orb stays on screen (with its
        spinner, until voice is ready) and no session finds voice gone and starts a second one. If exec
        can't happen, the old way: exit, and the session starts voice again."""
        try:
            handed = {"clients": OUTBOX.clients}
            if orb:
                orb.set_orb(voice="starting", opening=True)
                handed["helper"] = orb.hand_off()
            sock = INBOX.get("socket")
            if sock:
                sock.set_inheritable(True)
                handed["socket"] = sock.fileno()
            os.environ[HANDOFF_ENV] = json.dumps(handed)
            argv = [a for i, a in enumerate(sys.argv[1:], 1)
                    if a != "--restart" and not (i > 1 and sys.argv[i - 1] == "--restart")]
            logger.info(f"[lifecycle] restarting in place (pid {os.getpid()}): the orb and the inbox stay")
            sys.stdout.flush()
            sys.stderr.flush()
            os.execv(sys.executable, [sys.executable, SCRIPT, *argv, "--restart", why])
        except Exception as e:
            logger.error(f"[lifecycle] couldn't restart in place ({type(e).__name__}: {e}): exiting, to be started again")
            os.environ.pop(HANDOFF_ENV, None)
            os.kill(os.getpid(), signal.SIGTERM)

    def by_voice(action: str, value: str) -> tuple[bool, str]:
        # The menu's actions, asked for out loud (the talker's voice_menu): -> (done?, what happened).
        if action in ("show_transcript", "hide_transcript"):
            emit({"type": "panel", "show": action == "show_transcript"})
            return True, f"The transcript is {'shown' if action == 'show_transcript' else 'hidden'}."
        if action == "clear_conversation":
            emit({"type": "clear"})
            return True, "The conversation is cleared."
        if action == "language":
            code = settings.find_language(value)
            if not code:
                names = ", ".join(label for _, label, *_ in settings.LANGUAGES)
                return False, f"There's no language called {value!r}; the languages are {names}."
            if not settings.save("language", code):
                return False, "That language can't be picked."
            settings.save("voice", settings.voices(code)[0][0])
            asyncio.create_task(restart_when_quiet(f"the language is now {code}"))  # once your ack has played
            return True, "The language is changed; voice restarts in it in a moment, so it's quiet for a few seconds."
        if action == "voice":
            language = settings.load()["language"]
            vid = settings.find_voice(value, language)
            if not vid:
                names = ", ".join(label.split()[0] for _, label in settings.voices(language))
                return False, f"There's no voice called {value!r}; the voices are {names}."
            on_setting("voice", vid)
            return True, "The voice is changed; it speaks a short sample now."
        if action == "pass_on":
            mode = settings.find_mode(value)
            if not mode:
                return False, f"There's no such way to pass words on ({value!r}): it's Concise or Cleaned up."
            on_setting("pass_on", mode)
            return True, f"Their words now go on {'cleaned up' if mode == 'cleaned' else 'concisely'}."
        if action == "learn_voice":
            on_setting("more", "enroll")
            return True, "Learning their voice again: a window with phrases for them to read is opening."
        if action == "show_tour":
            on_setting("more", "tour")
            return True, "The tour is opening."
        if action == "progress":
            if value.strip().lower() not in ("on", "off"):
                return False, "Progress updates are either on or off."
            on_setting("more", f"progress_{value.strip().lower()}")
            return True, f"Progress updates are {value.strip().lower()}."
        if action == "next_session":
            return press_shortcut(action)
        if action == "tour_step":
            return step_tour(value)
        return False, "The menu has no such action."

    async def step_tour(step: str) -> tuple[bool, str]:
        # Next, back, a slide, done: handed to the open tour window (bin/onboarding watches tour-command).
        if not tour_is_open():
            return False, "The tour isn't open."
        step = step.strip().lower()
        step = {"close": "done", "previous": "back", "start": "first", "end": "last"}.get(step, step)
        home = TOUR_OPEN.parent
        (home / "tour-result").unlink(missing_ok=True)
        (home / "tour-command").write_text(step)
        for _ in range(15):
            await asyncio.sleep(0.1)
            if (home / "tour-result").exists():
                done = (home / "tour-result").read_text().strip()
                if done == "unknown":
                    return False, f"The tour has no step called {step!r}."
                if done.startswith("slide") and orb:
                    orb.tick()  # you hear the slide changed
                return True, "The tour is closed." if done == "closed" else done  # "slide N of 7: ...": the tour moved
            if not tour_is_open():  # done or close: the window was gone before it could answer
                return True, "The tour is closed."
        return False, "The tour didn't answer."

    async def press_shortcut(action: str) -> tuple[bool, str]:
        # "New session" (Cmd+N) and "next session" (Ctrl+Tab), pressed in the Claude app by the orb's helper.
        if not orb:
            return False, "Voice can't press the app's shortcuts without its orb helper running."
        how = await orb.press(action)
        if how == "done":
            return True, "A new session is opening in the app." if action == "new_session" else "The app moved to the next session."
        return False, {
            "no-access": "Nothing was pressed: Voice Assistant needs Accessibility permission. macOS asked; they allow Voice Assistant "
                         "in System Settings, Privacy & Security, Accessibility, then ask again.",
            "no-app": "Nothing was pressed: the Claude app isn't running.",
            "not-front": "Nothing was pressed: the Claude app didn't come to the front.",
        }.get(how, "Nothing was pressed: the helper didn't answer.")

    MENU["do"] = by_voice

    async def start_talking():
        # Start talking said on the Done screen: the window's own close presses it there (the done request, then
        # it fades out), as clicking it would. A window that can't answer still ends the first run.
        did, said = await step_tour("done")
        if not did:
            logger.info(f"[lifecycle] Start talking by voice: the window didn't answer ({said}), ending the first run here")
            end_first_run()

    if first_tour:
        FIRST_RUN["start"] = start_talking

    def start_new_session(message: str, folder: str, name: str):
        # A new session by voice: started and opened in the background; voice says when it's open.
        async def run():
            # Said 2s after it opens: after the switch to it, which starts voice afresh there.
            ok, said = await new_session.on_the_orb(orb, new_session.start(message, folder, name), settle=2)
            talker.on_session_event({"type": "new_session_done", "ok": ok, "text": said})
        asyncio.create_task(run())

    NEW_SESSION["start"] = start_new_session

    def go_to_session(name: str, project: str, session_id: str = ""):
        # "Go to <session>": opened from its folder with claude --desktop --resume; voice follows the focus.
        async def run():
            # Said 2s after it opens: after the switch, which starts voice afresh there.
            ok, said = await new_session.on_the_orb(orb, new_session.go_to(name, project, session_id), settle=2)
            talker.on_session_event({"type": "new_session_done", "ok": ok, "text": said})
        asyncio.create_task(run())

    NEW_SESSION["go"] = go_to_session

    if orb:
        TASKS.on_change = orb.set_tasks  # the orb's queued-task badge and panel
        orb.on_setting = on_setting
        orb.set_settings(orb_menu())
    try:
        await PipelineRunner(handle_sigint=True).run(task)
    except Exception as e:
        close_reason["why"] = f"crashed: {type(e).__name__}: {e}"
        raise  # logged with its traceback below main()
    except BaseException as e:  # Ctrl-C, or cancelled from outside
        close_reason["why"] = f"interrupted: {type(e).__name__}"
        raise
    finally:
        logger.info(f"[lifecycle] voice daemon exiting (pid {os.getpid()}): {close_reason['why']}")
        await inbox.cleanup()
        Path(socket_path).unlink(missing_ok=True)
        emit({"type": "status", "text": "stopped"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", default=str(home.SOCKET))
    ap.add_argument("--headphones", action="store_true")
    ap.add_argument("--no-aec", action="store_true")
    ap.add_argument("--restart", metavar="WHY", help="the mod restarts voice (it died, or the mod reloaded): carry on the conversation")
    args = ap.parse_args()
    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("LOG_LEVEL", "INFO"))
    # A file too: the mod only forwards stderr to the app's debug log, which usually isn't on.
    for folder in (home.RUN, new_session.FINISHED_DIR, new_session.WAITING_DIR, home.LOGS):
        home.private(folder)
    logger.add(home.LOG, level="INFO", rotation="5 MB", retention=2)
    logger.info(f"[lifecycle] voice daemon starting (pid {os.getpid()}, parent {os.getppid()})")
    try:
        asyncio.run(main(args.socket, not args.no_aec, args.headphones or os.environ.get("VOICE_HEADPHONES") == "1", args.restart))
    except Exception as e:
        # Into the file too: a crash while starting would otherwise reach only stderr, which the
        # "couldn't start" toast doesn't point to.
        logger.exception(f"[lifecycle] voice daemon crashed: {type(e).__name__}: {e}")
        sys.exit(1)
