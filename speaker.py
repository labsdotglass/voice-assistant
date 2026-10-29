"""Only your voice gets answered: each utterance is checked against your enrolled voice print.

ECAPA-TDNN (SpeechBrain) turns an utterance into a voice embedding in ~20 ms on the CPU, while
Whisper (~300 ms) transcribes the same audio, so the check adds nothing to the reply time.
`SpeakerCheck` sits beside `InterimTranscriber` (mic audio going down, VAD frames coming up)
and scores each utterance when it ends; `SpeakerGate`, after Whisper, drops the transcript of
one that isn't you, so the talker never hears it.

Enroll once with `uv run enroll_voice.py`. With no voice print, everyone is heard.
`uv run enroll_voice.py --live` enrolls from voice mode's own audio instead (after echo
cancellation, as every check hears you): while it captures, voice mode answers nobody.
The onboarding window's Your voice step, /voice-enroll and More > Learn my voice again do it phrase by
phrase: `EnrollPhrases`, after Whisper, checks each phrase it recognizes and keeps only their audio.
"""

import asyncio
import difflib
import os
import shutil
import threading
import time
import unicodedata
from pathlib import Path

import numpy as np
import torch
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import first_run
from home import HOME  # personal: never in the repo
VOICEPRINT = HOME / "voiceprint.npy"
CAPTURE = HOME / "capture"  # present: the next utterances build a new voice print (enroll_voice.py --live)
LIVE = HOME / "live"  # the captured utterances, a folder per language: the voice print is built from them all
STAGED = HOME / "live-new"  # learning by phrases collects here, and replaces a language's LIVE folder only once done
SKIPPED = HOME / "voice-skipped"  # Skip in the window: voice doesn't offer to learn your voice at start (the menu still can)
CAPTURE_SECS = 20  # of speech, at least, for a voice print
# Past that, the capture waits until you've been quiet this long, so you finish reading the card
# rather than being cut off mid-sentence; at CAPTURE_MAX_SECS of speech it ends anyway.
QUIET_SECS = 2.5
CAPTURE_MAX_SECS = 45
# What you read aloud to enroll, phrase by phrase in the onboarding window: how you'll talk to voice mode
# anyway, short everyday sentences, about 35 s aloud in all. The one-word answers come first: on their own they
# give the voice print pieces that sound like a lone "yes", which is what gets missed most. Each language has
# its own (the voice check scores lower in a language you didn't enroll in); the Hebrew ones address nobody
# as a man or a woman.
PHRASES = {
    "en": ["Hi, it's me. Yes. No. Do it. Stop.",
           "Let's get started. Please open the project and run the tests.",
           "If something fails, tell me what broke.",
           "Then fix it and show me the change.",
           "That looks good to me. Let's commit it and push to the main branch.",
           "What's taking so long? Give me a quick update.",
           "Find the bug on the login page and explain it simply.",
           "Thanks, I'll check back in a few minutes."],
    "he": ["היי, זה אני. כן. לא. יאללה. רגע, די.",
           "מתחילים. אפשר לפתוח את הפרויקט ולהריץ את הבדיקות?",
           "אם משהו נכשל, אני רוצה לדעת מה נשבר.",
           "אחר כך צריך לתקן את זה ולהראות לי את השינוי.",
           "נראה לי טוב. עושים קומיט ודוחפים לבראנץ' הראשי.",
           "מה לוקח כל כך הרבה זמן? אפשר עדכון קצר?",
           "צריך למצוא את הבאג בדף ההתחברות ולהסביר אותו בפשטות.",
           "תודה, אני אבדוק שוב בעוד כמה דקות."],
    "es": ["Hola, soy yo. Sí. No. Hazlo. Para.",
           "Empecemos. Abre el proyecto y ejecuta las pruebas.",
           "Si algo falla, dime qué se rompió.",
           "Luego arréglalo y muéstrame el cambio.",
           "Me parece bien. Hagamos commit y subámoslo a la rama principal.",
           "¿Qué está tardando tanto? Dame un resumen rápido.",
           "Busca el error en la página de inicio de sesión y explícalo fácil.",
           "Gracias, vuelvo a mirar en unos minutos."],
    "fr": ["Salut, c'est moi. Oui. Non. Vas-y. Arrête.",
           "On commence. Ouvre le projet et lance les tests.",
           "Si quelque chose échoue, dis-moi ce qui a cassé.",
           "Ensuite corrige-le et montre-moi le changement.",
           "Ça me va. On fait un commit et on pousse sur la branche principale.",
           "Qu'est-ce qui prend autant de temps ? Fais-moi un point rapide.",
           "Trouve le bug sur la page de connexion et explique-le simplement.",
           "Merci, je reviens voir dans quelques minutes."],
    "it": ["Ciao, sono io. Sì. No. Fallo. Fermati.",
           "Cominciamo. Apri il progetto ed esegui i test.",
           "Se qualcosa non va, dimmi cosa si è rotto.",
           "Poi correggilo e mostrami la modifica.",
           "Mi sembra a posto. Facciamo il commit e il push sul ramo principale.",
           "Cosa ci mette così tanto? Fammi un breve aggiornamento.",
           "Trova il bug nella pagina di accesso e spiegamelo in modo semplice.",
           "Grazie, ricontrollo tra qualche minuto."],
    "pt": ["Oi, sou eu. Sim. Não. Faça isso. Pare.",
           "Vamos começar. Abra o projeto e rode os testes.",
           "Se algo falhar, me diga o que quebrou.",
           "Depois corrija e me mostre a mudança.",
           "Parece bom para mim. Vamos fazer o commit e enviar para a branch principal.",
           "O que está demorando tanto? Me dá uma atualização rápida.",
           "Encontre o bug na página de login e explique de um jeito simples.",
           "Valeu, eu volto a olhar em alguns minutos."],
    "hi": ["नमस्ते, मैं हूँ। हाँ। नहीं। कर दो। रुको।",
           "चलो शुरू करते हैं। प्रोजेक्ट खोलो और टेस्ट चलाओ।",
           "अगर कुछ फ़ेल हो, तो बताओ क्या टूटा।",
           "फिर उसे ठीक करो और मुझे बदलाव दिखाओ।",
           "मुझे ठीक लग रहा है। चलो कमिट करके मेन ब्रांच पर पुश करते हैं।",
           "इतनी देर क्यों लग रही है? जल्दी से अपडेट दो।",
           "लॉगिन पेज में बग ढूँढो और आसान शब्दों में समझाओ।",
           "धन्यवाद, कुछ मिनट में फिर बात करते हैं।"],
}
# One more at a time, for a fast reader whose phrases came short of CAPTURE_SECS of speech.
EXTRA_PHRASES = {
    "en": ["Can you show me what changed since yesterday?", "Run it again and tell me if it passes.",
           "Let's leave that for now and move on."],
    "he": ["אפשר להראות לי מה השתנה מאתמול?", "מריצים שוב, ואני רוצה לדעת אם זה עובר.",
           "נשאיר את זה כרגע ונמשיך הלאה."],
    "es": ["¿Puedes mostrarme qué cambió desde ayer?", "Ejecútalo otra vez y dime si pasa.",
           "Dejemos eso por ahora y sigamos."],
    "fr": ["Tu peux me montrer ce qui a changé depuis hier ?", "Relance-le et dis-moi si ça passe.",
           "On laisse ça pour l'instant et on continue."],
    "it": ["Puoi mostrarmi cosa è cambiato da ieri?", "Rilancialo e dimmi se passa.",
           "Lasciamo stare per ora e andiamo avanti."],
    "pt": ["Você pode me mostrar o que mudou desde ontem?", "Rode de novo e me diga se passa.",
           "Vamos deixar isso por enquanto e seguir em frente."],
    "hi": ["कल से क्या बदला है, मुझे दिखा सकते हो?", "इसे फिर से चलाओ और बताओ पास हुआ या नहीं।",
           "अभी इसे छोड़ो और आगे बढ़ते हैं।"],
}
MATCH = 0.6  # of a phrase's words heard, in order, for it to be checked
TRIES = 3  # utterances aimed at a phrase, after which it's accepted on audio length alone (accent, noise)
SECS_PER_WORD = 0.25  # for that: at least this much speech per word of the phrase, well under a normal pace
SPOKEN_SECS_PER_WORD = 0.4  # a normal reading pace: a try's length is plausible at 0.5-2x the phrase's words at it


def phrases(language: str) -> list[str]:
    """The phrases to read in a language, in order."""
    return PHRASES.get(language, PHRASES["en"])


def words(text: str) -> list[str]:
    """A text's words as Whisper's and the phrase's can be compared: lower case, no punctuation, no Hebrew vowel points."""
    text = unicodedata.normalize("NFC", text).lower()
    out = []
    for ch in text:
        kind = unicodedata.category(ch)
        if ch in "'’׳`":
            continue  # it's -> its, c'est -> cest, as Whisper writes them either way
        if kind == "Mn" and "\u0590" <= ch <= "\u05ff":
            continue  # niqqud
        out.append(" " if kind[0] in "PSZ" else ch)
    return "".join(out).split()


def _same(a: str, b: str) -> bool:
    # A word Whisper spells a little differently (test / tests) is the same word.
    return a == b or (min(len(a), len(b)) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8)


def matches(heard: str, phrase: str) -> bool:
    """Whether what Whisper heard is the phrase: MATCH of its words or more, in order (the longest common run of them)."""
    p, h = words(phrase), words(heard)
    if not p or not h:
        return False
    row = [0] * (len(h) + 1)
    for a in p:
        prev, row = row, [0]
        for j, b in enumerate(h):
            row.append(prev[j] + 1 if _same(a, b) else max(prev[j + 1], row[j]))
    return row[-1] / len(p) >= MATCH


def _aimed_at(text: str, phrase: str, secs: float) -> bool:
    # A try at the phrase: nothing Whisper could make out, a word of it, or about its length aloud (a heavy accent
    # Whisper writes as other words). Other speech, with none of its words and the wrong length, never is. Only a
    # word of 4 letters or more counts: little ones ("the", "את", "से") are in almost any sentence. A piece all of
    # whose words are the phrase's is one too, however short: "Yes." said apart, as a phrase of short words invites.
    h, p = words(text), words(phrase)
    expected = len(p) * SPOKEN_SECS_PER_WORD
    return (not h or all(any(_same(a, b) for b in p) for a in h)
            or any(len(a) >= 4 and _same(a, b) for a in h for b in p) or 0.5 * expected <= secs <= 2 * expected)


class PhraseTracker:
    """Which phrases you've read. heard() takes each utterance's text and seconds of speech: it checks the phrase
    it matches (the current one, pieced together from the utterances since it became current if a pause split
    it, or any other still open), or after TRIES the current one on its length. `kept` then says how many of the
    latest utterances made that phrase, the ones whose audio goes into the voice print."""

    def __init__(self, phrases: list[str], extra: list[str] = ()):
        self.phrases = [{"text": p, "done": False} for p in phrases]
        self._extra, self._added = list(extra), 0
        self.secs = 0.0  # of speech in the checked phrases
        self.kept = 0
        self.aimed = False  # the last utterance was held as a try at the current phrase
        self._held: list[tuple[str, float]] = []  # utterances aimed at the current phrase, not yet matched

    @property
    def current(self) -> int:
        return next((i for i, p in enumerate(self.phrases) if not p["done"]), len(self.phrases))

    @property
    def done(self) -> bool:
        return self.current == len(self.phrases)

    def state(self) -> dict:
        return {"phrases": [dict(p) for p in self.phrases], "current": self.current}

    def heard(self, text: str, secs: float) -> int | None:
        now = self.current
        self.aimed = False
        if now == len(self.phrases):
            return None
        for k in range(len(self._held) + 1):  # this utterance alone, then with the ones before it
            pieces = self._held[len(self._held) - k:] + [(text, secs)]
            if matches(" ".join(t for t, _ in pieces), self.phrases[now]["text"]):
                return self._check(now, pieces)
        for i, p in enumerate(self.phrases):
            if i != now and not p["done"] and matches(text, p["text"]):
                return self._check(i, [(text, secs)])
        self.kept = 0
        if not _aimed_at(text, self.phrases[now]["text"], secs):
            return None  # someone else, the TV, a question: not a try, and never kept
        self.aimed = True
        self._held.append((text, secs))
        held = self._held
        if len(held) >= TRIES and sum(s for _, s in held) >= SECS_PER_WORD * len(words(self.phrases[now]["text"])):
            return self._check(now, held)
        return None

    def _check(self, i: int, pieces: list[tuple[str, float]]) -> int:
        if i == self.current:
            self._held = []
        self.phrases[i]["done"] = True
        self.kept = len(pieces)
        self.secs += sum(s for _, s in pieces)
        if self.done and self.secs < CAPTURE_SECS and self._extra:  # short of a voice print's speech: one more
            self.phrases.append({"text": self._extra[self._added % len(self._extra)], "done": False})
            self._added += 1
        return i


def enrolling_language() -> str:
    """The language of the live enrollment under way (or the last one): CAPTURE holds it."""
    try:
        return CAPTURE.read_text().strip() or "en"
    except OSError:
        return "en"


def enrolled_languages() -> set[str]:
    """The languages your voice print has recordings in."""
    return {d.name for d in LIVE.iterdir() if d.is_dir() and any(d.glob("*.wav"))} if LIVE.exists() else set()


def should_offer(language: str) -> bool:
    """Whether a start (outside a first run) opens the window to learn your voice: not after Skip; with no voice
    print, or none of your recordings in this language (the check scores lower in one you didn't enroll in)."""
    return not SKIPPED.exists() and (not VOICEPRINT.exists() or language not in enrolled_languages())


def start_enrollment(language: str = "en"):
    """Turns on live enrollment in a language: the next ~20 s of speech replace that language's recordings,
    and the voice print is rebuilt from every language's."""
    CAPTURE.parent.mkdir(parents=True, exist_ok=True)
    for f in (LIVE / language).glob("*.wav"):
        f.unlink()
    CAPTURE.write_text(language)  # of speech: the first voice print came from 15
# Similarity to the voice print's best piece (1 = same voice) at or above which an utterance is yours.
# Measured on the MacBook mic: another person reaches 0.32 from 1 s of speech, 0.42 from a 0.3 s word;
# at 0.33 a one-word answer of yours is missed 16% of the time, and another person's gets in 6%.
CUTOFF = float(os.environ.get("SPEAKER_CUTOFF", "0.33"))
MIN_SECS = 0.6  # shorter than this says too little about the voice: let it through
# The voice print is many prints, one per half second of your speech, and an utterance scores its
# best match: a one-word "yes" sounds like a short piece of you far more than like all of you
# averaged together (on your enrollment audio, a 0.3 s word was missed 44% of the time by one
# averaged print, 14% by the pieces).
PIECE_SECS = 0.5


def pieces(audio: np.ndarray) -> list[np.ndarray]:
    """The half-second stretches of speech in 16 kHz audio, a quarter second apart (quiet ones skipped)."""
    n = int(PIECE_SECS * 16000)
    windows = [audio[i:i + n] for i in range(0, len(audio) - n + 1, n // 2)]
    if not windows:
        return [audio] if len(audio) else []
    level = np.array([np.sqrt(np.mean(w ** 2)) for w in windows])
    return [w for w, v in zip(windows, level) if v >= 0.4 * np.percentile(level, 90)]


ECAPA_REPO = "speechbrain/spkrec-ecapa-voxceleb"
ECAPA_REVISION = "0f99f2d0ebe89ac095bcc5903c4dd8f72b367286"
_encoder = None
_encoder_lock = threading.Lock()


def load_encoder():
    """ECAPA, downloaded (Hugging Face's cache: whole files only) and loaded once."""
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            from huggingface_hub import snapshot_download
            from speechbrain.inference.speaker import EncoderClassifier

            # At a fixed commit: its hyperparams.yaml builds Python objects, so a changed repo could run code here.
            # speechbrain can't pin one itself, so it loads the copy fetched at that commit.
            source = snapshot_download(ECAPA_REPO, revision=ECAPA_REVISION, allow_patterns=["hyperparams.yaml", "*.ckpt", "label_encoder.txt"])
            _encoder = EncoderClassifier.from_hparams(source, run_opts={"device": "cpu"}, savedir=str(HOME / "ecapa"))
        return _encoder


class VoicePrint:
    def __init__(self, lazy: bool = False):
        # lazy: ECAPA loads on first use (a first run's setup fetches it, reporting to the window)
        if not lazy:
            load_encoder()
        # (pieces, 192); a print saved before the pieces is a single row, and still works
        self.print = torch.from_numpy(np.atleast_2d(np.load(VOICEPRINT))) if VOICEPRINT.exists() else None

    def embed(self, audio: np.ndarray) -> torch.Tensor:
        """A unit-length voice embedding of 16 kHz float audio."""
        wav = torch.from_numpy(audio / (np.abs(audio).max() + 1e-9) * 0.9).float()
        with torch.no_grad():
            return torch.nn.functional.normalize(load_encoder().encode_batch(wav.unsqueeze(0)).squeeze(), dim=0)

    def make(self, recordings: list[np.ndarray]) -> torch.Tensor:
        """A voice print from recordings of one voice: an embedding for each half second of speech."""
        return torch.stack([self.embed(p) for r in recordings for p in pieces(r)])

    def score(self, audio: np.ndarray) -> float:
        return float((self.print @ self.embed(audio)).max())

    def upgrade(self) -> bool:
        """Rebuilds a single-row print (saved before the pieces) from the live enrollment that made it, if
        that's still on disk: its last utterance was saved moments before the print. True if it did."""
        import soundfile as sf

        wavs = sorted(LIVE.rglob("*.wav"), key=lambda f: f.stat().st_mtime) if LIVE.exists() else []
        if self.print is None or len(self.print) > 1 or not wavs:
            return False
        if abs(VOICEPRINT.stat().st_mtime - wavs[-1].stat().st_mtime) > 60:  # the print came from elsewhere
            return False
        self.print = self.make([sf.read(f, dtype="float32")[0] for f in wavs])
        np.save(VOICEPRINT, self.print.numpy())
        return True

    def capture(self, audio: np.ndarray, folder: Path | None = None) -> float:
        """Keeps one utterance for a live enrollment (in folder, else the language's LIVE one). Returns the seconds
        of speech captured there so far."""
        import soundfile as sf

        folder = folder or LIVE / enrolling_language()
        folder.mkdir(parents=True, exist_ok=True)
        sf.write(folder / f"{time.time():.3f}.wav", audio, 16000)
        return sum(sf.info(f).frames for f in folder.glob("*.wav")) / 16000

    def finish(self):
        """Ends a live enrollment: the captured utterances become the voice print, in use at once."""
        import soundfile as sf

        self.print = self.make([sf.read(f, dtype="float32")[0] for f in sorted(LIVE.rglob("*.wav"))])
        np.save(VOICEPRINT, self.print.numpy())
        CAPTURE.unlink(missing_ok=True)


class Verdict:
    """The last utterance's check, shared by SpeakerCheck (writes) and SpeakerGate (reads)."""

    def __init__(self):
        self.is_you = True
        self.matched = False  # scored as your voice, not just let through (too short to judge, no print)
        self.ready = asyncio.Event()
        self.ready.set()


class SpeakerCheck(FrameProcessor):
    PREROLL_SECS = 0.5  # VAD fires a little after speech starts

    def __init__(self, voice: VoicePrint, verdict: Verdict, on_enrolled=None, on_progress=None, **kwargs):
        super().__init__(**kwargs)
        self._on_enrolled = on_enrolled  # called once a live enrollment has made the voice print
        self._on_progress = on_progress  # called with the seconds captured so far, during a live enrollment
        self._voice = voice
        self._verdict = verdict
        self._recent = bytearray()
        self._speech = bytearray()
        self._rate = 16000
        self._speaking = False
        self._finishing = None  # a live enrollment with enough speech, waiting for you to finish reading
        self._ending = False  # the voice print is being made from the captured speech
        self.phrases = None  # the EnrollPhrases after Whisper: while it has phrases out, utterances go to it
        self.level = 0.0  # the mic's level, 0-1, while it does (the window's orb moves with it)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self._rate = frame.sample_rate
            if self.phrases and self.phrases.tracker:
                self.level = max(level_of(np.frombuffer(frame.audio, dtype=np.int16) / 32768), self.level * 0.8)
            if self._speaking:
                self._speech += frame.audio
            else:
                self._recent += frame.audio
                del self._recent[: max(0, len(self._recent) - int(self.PREROLL_SECS * self._rate) * 2)]
        elif isinstance(frame, VADUserStartedSpeakingFrame) and not self._speaking:
            self.speech_started()
            self._speech = bytearray(self._recent)
            self._verdict.ready.clear()  # the gate waits for this utterance's check
            self._verdict.matched = False  # not yet, nor if the check runs late
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and self._speaking:
            self._speaking = False
            self.create_task(self._check(bytes(self._speech), self._rate))
        await self.push_frame(frame, direction)

    def speech_started(self):
        self._speaking = True
        if self._finishing:  # still reading: the enrollment waits for this sentence too
            self._finishing.cancel()
            self._finishing = None

    async def _finish_enrollment(self):
        self._ending = True  # what's said while the print is made isn't captured
        try:
            await asyncio.to_thread(self._voice.finish)
        finally:
            self._ending = False
        logger.info(f"[speaker] live enrollment done: new voice print in use ({VOICEPRINT})")
        if self._on_enrolled:
            self._on_enrolled()

    async def _finish_when_quiet(self):
        await asyncio.sleep(QUIET_SECS)
        self._finishing = None  # from here on, speaking again doesn't cancel it
        await self._finish_enrollment()

    async def _check(self, pcm: bytes, rate: int):
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
        secs = len(audio) / rate
        try:
            if rate != 16000:
                import torchaudio

                audio = torchaudio.functional.resample(torch.from_numpy(audio), rate, 16000).numpy()
            if CAPTURE.exists():  # live enrollment: keep it, answer nobody meanwhile
                self._verdict.is_you = False
                if self.phrases and self.phrases.tracker:  # by phrases: kept once Whisper's text says which
                    self.phrases.utterance(audio, secs)
                    return
                if secs >= MIN_SECS and not self._ending:
                    got = await asyncio.to_thread(self._voice.capture, audio)
                    logger.info(f"[speaker] live enrollment: {got:.0f} s captured (needs {CAPTURE_SECS})")
                    if self._on_progress:
                        self._on_progress(min(got, CAPTURE_SECS))  # the bar stays full while you finish reading
                    if got >= CAPTURE_MAX_SECS:
                        await self._finish_enrollment()
                    elif got >= CAPTURE_SECS and not self._speaking:  # not if your next sentence already began
                        self._finishing = asyncio.get_running_loop().create_task(self._finish_when_quiet())
                return
            if secs < MIN_SECS or self._voice.print is None:
                self._verdict.is_you = True
                return
            t0 = time.perf_counter()
            score = await asyncio.to_thread(self._voice.score, audio)
            self._verdict.is_you = self._verdict.matched = score >= CUTOFF
            logger.info(
                f"[speaker] {'you' if self._verdict.is_you else 'not you'}: similarity {score:.2f} "
                f"(cutoff {CUTOFF}), {secs:.1f}s utterance, checked in {(time.perf_counter() - t0) * 1000:.0f} ms"
            )
        except Exception as e:  # never lock you out over a failed check
            logger.warning(f"[speaker] check failed, letting it through: {e}")
            self._verdict.is_you = True
        finally:
            self._verdict.ready.set()


def swap_in(language: str):
    """The phrases' recordings, complete, replace that language's in LIVE."""
    folder = LIVE / language
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.glob("*.wav"):
        f.unlink()
    for f in (STAGED / language).glob("*.wav"):
        f.rename(folder / f.name)
    shutil.rmtree(STAGED / language, ignore_errors=True)


def level_of(audio: np.ndarray) -> float:
    """How loud audio is, 0 (-60 dBFS or quieter) to 1 (-10 dBFS): its loudest 50 ms stretches, not its pauses."""
    n = 800
    rms = [np.sqrt(np.mean(audio[i:i + n] ** 2)) for i in range(0, max(len(audio) - n + 1, 1), n)] if len(audio) else [0.0]
    loud = float(np.percentile(rms, 80))
    return float(np.clip((20 * np.log10(loud + 1e-9) + 60) / 50, 0, 1))


LOUDER = 0.3  # an utterance below this level (about -45 dBFS) is too quiet: "a bit louder"
KEEP_GOING_SECS = 4  # quiet this long with phrases left: "keep going"
TRANSCRIBED_SECS = 4  # an utterance with no text from Whisper by then counts as a try with none
EVERY = 0.5  # seconds between the window's state writes while enrolling
WINDOW_SECS = 30  # enrollment that never sees its window by then (it didn't open) stops


class EnrollPhrases(FrameProcessor):
    """After Whisper, while voice learns your voice by phrases: SpeakerCheck hands it each utterance's audio,
    Whisper's text says which phrase it was, and only a recognized phrase's audio is kept, so another voice or
    a slide read aloud isn't learned. The onboarding window draws the state it writes (first_run.write "voice":
    the phrases, the current one, the mic level and a hint) on each utterance and every EVERY seconds;
    with every phrase read, the voice print is made and it writes "voice-done"."""

    def __init__(self, voice: VoicePrint, verdict: Verdict, check, on_enrolled=None, window_open=None, on_gone=None, **kwargs):
        super().__init__(**kwargs)
        self._voice, self._verdict, self._check = voice, verdict, check
        self._on_enrolled = on_enrolled  # called once the voice print is made
        self._window_open = window_open  # the onboarding window is up: enrollment without it stops
        self._on_gone = on_gone  # called when it stops so
        self.tracker: PhraseTracker | None = None
        self.language = None
        self._fields: dict = {}  # more for the window's state (again: outside a first run)
        self._pending: list[tuple[np.ndarray, float, float]] = []  # utterances waiting for Whisper's text
        self._held: list[np.ndarray] = []  # the audio of those aimed at the current phrase, not yet matched
        self._hint, self._last = "hearing", time.monotonic()
        self._started = time.monotonic()
        self._ticker = None
        self._finishing = False

    def start(self, language: str, **fields) -> bool:
        """Learns your voice in a language from its phrases: -> whether it started. Again while it does, in the same
        language: no change (/voice-enroll starts it, then the window it opens asks too)."""
        if self.tracker and language == self.language:
            self.publish()
            return False
        self.stop(quiet=True)
        CAPTURE.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(STAGED / language, ignore_errors=True)
        CAPTURE.write_text(language)  # the recordings already there stay until the new ones are complete
        self.tracker = PhraseTracker(phrases(language), EXTRA_PHRASES.get(language, EXTRA_PHRASES["en"]))
        self.language, self._fields = language, fields
        self._hint, self._last, self._started = "hearing", time.monotonic(), time.monotonic()
        self._ticker = asyncio.get_running_loop().create_task(self._tick())
        logger.info(f"[speaker] learning the user's voice by phrases, in {language}")
        self.publish()
        return True

    def stop(self, quiet: bool = False, **fields):
        """Ends it without a voice print (Skip, Later, the window gone). quiet: the window isn't told."""
        if self._ticker:
            self._ticker.cancel()
            self._ticker = None
        was, self.tracker = self.tracker, None
        self._pending, self._held = [], []
        if was and not self._finishing:
            CAPTURE.unlink(missing_ok=True)
            shutil.rmtree(STAGED / self.language, ignore_errors=True)  # a part read: what was there stays
            logger.info("[speaker] learning the user's voice stopped, no voice print made")
        if not quiet:
            first_run.write("voice-done", learned=False, **(fields or self._fields))

    def publish(self):
        if self.tracker:
            first_run.write("voice", **self.tracker.state(), level=round(self._check.level, 2), hint=self.hint(), **self._fields)

    def hint(self) -> str:
        if self._check._speaking:
            return "louder" if self._check.level < LOUDER else "hearing"
        if time.monotonic() - self._last > KEEP_GOING_SECS:
            return "keep_going"
        return self._hint

    def utterance(self, audio: np.ndarray, secs: float):
        """SpeakerCheck's: one utterance's 16 kHz audio, kept until Whisper's text for it comes."""
        self._pending.append((audio, secs, time.monotonic()))

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and self.tracker:
            await self.transcribed(frame)
        await self.push_frame(frame, direction)  # SpeakerGate drops it: nobody is answered meanwhile

    async def transcribed(self, frame: TranscriptionFrame):
        came = time.monotonic()
        try:
            await asyncio.wait_for(self._verdict.ready.wait(), 1.0)  # SpeakerCheck has handed the audio over
        except TimeoutError:
            pass
        # The text is the newest utterance handed over before it: Whisper sends no frame for one it made nothing
        # of (a cough), so those older ones are tries with no text. Utterances after it wait for their own.
        mine = [u for u in self._pending if u[2] <= came] or self._pending[:1]
        self._pending = self._pending[len(mine):]
        for i, (audio, secs, _) in enumerate(mine):
            await self._heard(frame.text if i == len(mine) - 1 else "", audio, secs)

    async def _heard(self, text: str, audio: np.ndarray, secs: float):
        tracker = self.tracker
        if not tracker or tracker.done:
            return
        now = tracker.current
        checked = tracker.heard(text, secs)
        self._hint, self._last = ("louder" if level_of(audio) < LOUDER else "hearing"), time.monotonic()
        if checked is None:
            if tracker.aimed:
                self._held.append(audio)  # perhaps the start of the current phrase, split by a pause
        else:
            keep = [audio]
            if checked == now:  # the utterances that made it: this one, and those before it if it took them
                keep, self._held = (self._held + [audio])[-tracker.kept:], []
            logger.info(f"[speaker] phrase {checked + 1} recognized: {text.strip()!r}")
            for piece in keep:
                await asyncio.to_thread(self._voice.capture, piece, STAGED / self.language)
        self.publish()
        if tracker.done and self.tracker is tracker:
            await self._finish()

    async def _finish(self):
        self._finishing = True
        try:
            await asyncio.to_thread(swap_in, self.language)
            await asyncio.to_thread(self._voice.finish)  # waits for ECAPA if setup is still loading it
            SKIPPED.unlink(missing_ok=True)
            fields = self._fields
            self.stop(quiet=True)
            first_run.write("voice-done", learned=True, **fields)
        finally:
            self._finishing = False
        logger.info(f"[speaker] learned the user's voice by phrases: new voice print in use ({VOICEPRINT})")
        if self._on_enrolled:
            self._on_enrolled()

    async def _tick(self):
        seen = False
        while self.tracker:
            await asyncio.sleep(EVERY)
            now = time.monotonic()
            while self._pending and now - self._pending[0][2] > TRANSCRIBED_SECS:  # Whisper had nothing for it
                audio, secs, _ = self._pending.pop(0)
                await self._heard("", audio, secs)
            if self._window_open:
                if self._window_open():
                    seen = True
                elif seen or now - self._started > WINDOW_SECS:
                    logger.info("[speaker] the onboarding window is gone: learning the user's voice stops")
                    self.stop(quiet=True)
                    if self._on_gone:
                        self._on_gone()
                    return
            self.publish()


class SpeakerGate(FrameProcessor):
    """After Whisper: drops what another voice said, so the talker doesn't answer it."""

    def __init__(self, verdict: Verdict, **kwargs):
        super().__init__(**kwargs)
        self._verdict = verdict

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            try:
                await asyncio.wait_for(self._verdict.ready.wait(), 1.0)  # done long before Whisper
            except TimeoutError:
                logger.warning("[speaker] no verdict in time, letting it through")
            if not self._verdict.is_you:
                logger.info(f"[speaker] not answering another voice: {frame.text!r}")
                return
        await self.push_frame(frame, direction)
