"""The orb menu's settings (no model): Voice and Pass on my words are kept, checked, and reach the talker and the orb."""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import os
import json
import sys
import tempfile
from pathlib import Path

import settings
import talker
from daemon import until_quiet, voice_language
from helper_transport import AudioHelper
from pipecat.transcriptions.language import Language

settings.FILE = Path(tempfile.mkdtemp()) / "settings.json"  # not your real settings
results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


async def main():
    ok(settings.load() == {"language": "en", "voice": "af_heart", "pass_on": "concise"}, "with nothing saved: English, Heart, Concise")
    ok(len(settings.VOICES) == 8, "eight voices to choose from")
    ok(settings.save("voice", "am_eric") and settings.load()["voice"] == "am_eric", "a picked voice is kept")
    ok(not settings.save("voice", "xx_nobody") and settings.load()["voice"] == "am_eric", "an unknown voice is ignored")
    ok(settings.save("language", "he") and settings.load()["voice"] == "he_shaul", "Hebrew: an English voice gives way to Hebrew's")
    ok(settings.menu(settings.load())["voices"] == [["he_shaul", "Shaul (male)"]] or settings.menu(settings.load())["voices"] == [("he_shaul", "Shaul (male)")],
       "Hebrew: the menu offers only Hebrew's voices")
    ok(settings.find_language("Hebrew") == "he" and settings.find_language("עברית") == "he" and settings.find_language("Klingon") is None,
       "a language by its spoken name")
    ok("Hebrew" in settings.language_rule("he") and settings.language_rule("en") == "", "the talker is told to speak Hebrew; nothing for English")
    ok(not settings.save("language", "xx") and settings.load()["language"] == "he", "an unknown language is ignored")
    settings.save("language", "en")
    settings.save("voice", "am_eric")
    ok(settings.save("pass_on", "cleaned") and settings.load() == {"language": "en", "voice": "am_eric", "pass_on": "cleaned"}, "both settings kept together")
    settings.FILE.write_text("{broken")
    ok(settings.load()["pass_on"] == "concise", "a broken settings file falls back to the defaults")
    ok(voice_language("bf_emma") == Language.EN_GB and voice_language("am_michael") == Language.EN_US, "British voices speak British English")

    ok(settings.find_voice("Michael") == "am_michael" and settings.find_voice("kore") == "af_kore" and settings.find_voice("Puck") is None,
       "a voice by its spoken name; one not in the menu is no voice")
    ok(settings.find_mode("cleaned up") == "cleaned" and settings.find_mode("Concise") == "concise" and settings.find_mode("loud") is None,
       "a Pass on my words mode by its spoken name")
    menu = settings.menu({"voice": "af_bella", "pass_on": "concise"})
    ok(json.loads(json.dumps(menu))["voices"][1] == ["af_bella", "Bella (female)"], "the orb menu gets ids and labels")
    ok(all(v.startswith(("af_", "am_")) for v, _ in settings.VOICES), "only American voices")
    ok(sum(v.startswith("af_") for v, _ in settings.VOICES) == 4 and sum(v.startswith("am_") for v, _ in settings.VOICES) == 4, "four female and four male")

    # The orb menu's command lines reach the daemon as settings.
    picked = []

    class Proc:
        async def wait(self): return 0
    proc = Proc()

    async def lines():
        for l in [b"audio_helper: command: language he\n", b"audio_helper: command: voice af_sarah\n", b"audio_helper: command: pass-on cleaned\n",
                  b"audio_helper: command: more enroll\n", b"audio_helper: command: more tour\n", b"audio_helper: command: more progress\n"]:
            yield l
    proc.stderr = lines()
    helper = AudioHelper(24000)
    helper.proc, helper.on_setting = proc, lambda k, v: picked.append((k, v))
    await helper._log_stderr(proc)
    ok(picked == [("language", "he"), ("voice", "af_sarah"), ("pass_on", "cleaned"), ("more", "enroll"), ("more", "tour"), ("more", "progress")],
       f"the menu's picks reach the daemon, Language and More's included: {picked}")

    # The talker's claude starts in voice's own folder: started in a project it loads that project's memory,
    # which once had it forward "Thanks." to the session in half the runs (test_end_voice.py).
    auth, talker.ensure_subscription_auth = talker.ensure_subscription_auth, lambda: None
    ok(Path(talker.make_options().cwd) == talker.HOME, "the talker's claude starts in ~/.voice-assistant, not in a project")
    talker.ensure_subscription_auth = auth
    talker.PASS_ON.update(mode="concise", note="")
    ok("as few words as carry it" in talker.system_prompt() and "<<PASS_ON>>" not in talker.system_prompt(), "Concise: the talker's prompt asks for few words")
    talker.set_pass_on("cleaned")
    ok("own wording, cleaned up" in talker.system_prompt(), "Cleaned up: the talker's prompt asks for your own wording")
    ok("own wording, cleaned up" in talker.PASS_ON["note"], "switched while voice runs: a one-time note tells the talker")
    talker.PASS_ON.update(mode="concise", note="")

    # The model sometimes calls send_to_session twice in one reply: the session gets it once.
    import io
    from contextlib import redirect_stdout
    talker.OUTBOX.clients = {}
    buf = io.StringIO()
    with redirect_stdout(buf):
        for _ in range(2):
            await talker.send_to_session.handler({"ack": "", "prompt": "Run the tests."})
    ok(buf.getvalue().count('"submit"') == 1, "the same prompt sent twice in a row reaches the session once")

    # The same tool asked for twice in one reply (Haiku does, now and then) runs once.
    ran = []
    talker.MENU["do"] = lambda action, value: (ran.append(action), (True, "Done."))[1]
    talker.REPLY_CALLS.clear()
    with redirect_stdout(io.StringIO()):
        for _ in range(2):
            await talker.voice_menu.handler({"ack": "Opening the tour.", "action": "show_tour", "value": ""})
        await talker.voice_menu.handler({"ack": "And the transcript.", "action": "show_transcript", "value": ""})
    ok(ran == ["show_tour", "show_transcript"], f"a repeated call in one reply runs once; a different one runs: {ran}")
    talker.FEED.drain()  # the next reply
    with redirect_stdout(io.StringIO()):
        await talker.voice_menu.handler({"ack": "Again.", "action": "show_tour", "value": ""})
    ok(ran.count("show_tour") == 2, "asked again in a new reply, it runs again")

    # Through the live speech path: a tour step's ack is never spoken, even when the model writes one.
    from claude_agent_sdk.types import StreamEvent
    from pipecat.frames.frames import LLMTextFrame

    class Fake:
        async def query(self, text): pass

        async def receive_response(self):
            yield StreamEvent(uuid="u", session_id="s", event={"type": "content_block_start", "content_block": {"type": "tool_use", "name": self.tool}})
            for c in self.chunks:
                yield StreamEvent(uuid="u", session_id="s", event={"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": c}})
            yield StreamEvent(uuid="u", session_id="s", event={"type": "message_stop"})

    async def spoken(tool, chunks):
        t, heard = talker.Talker(), []
        fake = Fake()
        fake.tool, fake.chunks = tool, chunks
        t._client = fake

        async def push(frame, *a):
            if isinstance(frame, LLMTextFrame):
                heard.append(frame.text)
        t.push_frame = push
        with redirect_stdout(io.StringIO()):
            await t._reply("x")
        return "".join(heard).strip()

    said = await spoken("mcp__voice__voice_menu", ['{"ack": "Moving on to', ' the next slide.", "act', 'ion": "tour_step", "value": "next"}'])
    ok(not said, f"a tour step's ack isn't spoken, even when written ({said!r})")
    said = await spoken("mcp__voice__voice_menu", ['{"ack": "Hiding the', ' transcript now.", "action": "hide_', 'transcript", "value": ""}'])
    ok(said == "Hiding the transcript now.", f"another menu call's ack is spoken, once its action is known ({said!r})")
    said = await spoken("mcp__voice__send_to_session", ['{"ack": "Sending that', ' over now.", "prompt": "Run the tests."}'])
    ok(said == "Sending that over now.", f"other tools' acks are spoken as before ({said!r})")

    # Going to a session by name: the app's session records, matched by title words and project.
    import new_session
    root = Path(tempfile.mkdtemp())
    for i, (title, cwd, archived) in enumerate([("Uncommitted changes review", "/u/dev/voice-assistant", False),
                                                ("Wireframes editor", "/u/Atlas", False),
                                                ("Wireframes and research reports index", "/u/dev/payments-api", False),
                                                ("Billing notes", "/u/dev/payments-api", True)]):
        f = root / "a" / "b" / f"local_{i}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"cliSessionId": f"id{i}", "cwd": cwd, "title": title, "isArchived": archived, "lastFocusedAt": i}))
    hit, _ = new_session.find_session("the uncommitted changes review", root=root)
    ok(hit and hit["id"] == "id0" and hit["cwd"] == "/u/dev/voice-assistant", "a session found by its spoken name, with its folder")
    hit, closest = new_session.find_session("wireframes", root=root)
    ok(hit is None and len(closest) == 2, f"two sessions fit equally: ask which ({closest})")
    hit, _ = new_session.find_session("wireframes", "payments api", root=root)
    ok(hit and hit["id"] == "id2", "the project named settles it")
    ok(new_session.find_session("banana bread", root=root) == (None, []), "nothing like it: nothing found")
    ok(new_session.find_session("billing notes", root=root)[0] is None, "an archived session isn't offered")
    # A project's name: its last active session, not one whose title shares the word.
    proj = Path(tempfile.mkdtemp())
    for i, (title, cwd, active) in enumerate([("Acme notes", "/u/dev/acme-site", 900),  # shares the word, other project
                                              ("API migration", "/u/dev/acme", 500),
                                              ("Docs pass", "/u/dev/acme", 800)]):
        f = proj / "a" / "b" / f"local_{i}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"cliSessionId": f"p{i}", "cwd": cwd, "title": title, "lastActivityAt": active, "lastFocusedAt": 1}))
    hit, _ = new_session.find_session("acme", root=proj)
    ok(hit and hit["id"] == "p2", f"a project's name goes to its last active session ({hit and hit['title']})")
    hit, _ = new_session.find_session("", "acme", root=proj)
    ok(hit and hit["id"] == "p2", "a project alone, no name: the same")
    hit, _ = new_session.find_session("acme notes", root=proj)
    ok(hit and hit["id"] == "p0", "a session's own name still finds that session")

    # ~/.voice-assistant is private to you: it holds recordings of your voice.
    import home
    folder = Path(tempfile.mkdtemp()) / "va"
    folder.mkdir(mode=0o755)
    ok(home.private(folder).stat().st_mode & 0o777 == 0o700, "voice's own folder is made private (0700)")

    # Auto mode for new sessions: reported, turned on only when asked, the rest of the settings kept, a copy left.
    import auto_mode
    import onboarding
    real_home = os.environ["HOME"]
    os.environ["HOME"] = str(Path(tempfile.mkdtemp()))
    try:
        ok(auto_mode.mode() == "default", "no settings file: auto mode is off")
        ok("on" in auto_mode.turn_on() and auto_mode.mode() == "auto", "turned on, it creates the file")
        f = auto_mode.settings_file()
        f.write_text(json.dumps({"model": "opus", "permissions": {"allow": ["Bash(ls)"], "defaultMode": "acceptEdits"}}))
        auto_mode.turn_on()
        kept = json.loads(f.read_text())
        ok(kept["model"] == "opus" and kept["permissions"]["allow"] == ["Bash(ls)"] and kept["permissions"]["defaultMode"] == "auto",
           "turned on over another mode: every other setting is kept")
        ok(json.loads(f.with_name("settings.json.before-auto-mode").read_text())["permissions"]["defaultMode"] == "acceptEdits",
           "and a copy of the file as it was is left next to it")
        ok("already" in auto_mode.turn_on(), "already on: nothing is written")
        f.write_text("{not json")
        ok("not changed" in auto_mode.turn_on() and f.read_text() == "{not json", "a broken settings file is left as it is")
        f.unlink()
    finally:
        os.environ["HOME"] = real_home

    # A language picked from the orb's menu while voice speaks waits for the reply to end, a few seconds at most.
    speaking = {"until": 3}
    def busy():
        speaking["until"] -= 1
        return speaking["until"] >= 0
    waited = await until_quiet(busy, most=1.0, step=0.01)
    ok(0.02 <= waited < 0.1, f"a restart waits while voice is speaking, then goes ({waited:.2f}s)")
    waited = await until_quiet(lambda: True, most=0.05, step=0.01)
    ok(waited >= 0.05 and waited < 0.1, f"a reply that never ends doesn't hold the restart past the limit ({waited:.2f}s)")
    ok(await until_quiet(lambda: False) == 0, "voice quiet: the restart goes at once")

    # Experimental languages: at the bottom of the menu's Language list, below a divider, marked.
    langs = settings.menu({"language": "en"})["languages"]
    ok(langs[-2:] == [settings.MENU_DIVIDER, ["he", "Hebrew (experimental)"]],
       f"Hebrew is last in the Language menu, below a divider, marked experimental: {langs[-3:]}")
    ok(settings.MENU_DIVIDER not in langs[:-2] and all("(" not in label for _, label in langs[:-2]),
       "the other languages are above it, unmarked")
    ok(settings.find_language("Hebrew") == "he" and settings.language("he")[1] == "Hebrew",
       "only the menu's label changes: voice still finds it, and names it, as Hebrew")

    # The languages live in languages.json (one source, with each one's download size); settings and onboarding read it.
    import onboarding
    listed = json.loads((Path(__file__).resolve().parent.parent / "languages.json").read_text())
    ok([l["code"] for l in listed["languages"]] == [c for c, *_ in settings.LANGUAGES], "languages.json and settings.LANGUAGES list the same languages in the same order")
    ok(all(l["download_gb"] > 0 and sum(l["repos"].values()) > 0 for l in listed["languages"]), "every language has a download size and repos")
    ok(all(round((sum(l["repos"].values()) + sum(listed["shared_files"].values())) / 1e9, 1) == l["download_gb"] for l in listed["languages"]),
       "every language's download_gb is its repos plus the shared files, so an edit to one can't leave the other stale")
    ok(settings.download_gb("he") == 2.8 and settings.download_gb("en") == 4.0,
       "download sizes as measured (Hebrew skips Whisper large-v3; the Kokoro ONNX files count for all)")
    ok([l["code"] for l in onboarding.languages()] == [c for c, *_ in settings.LANGUAGES if c not in settings.EXPERIMENTAL] + sorted(settings.EXPERIMENTAL),
       "onboarding lists the languages in menu order, experimental last")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
