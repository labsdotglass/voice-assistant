"""Kokoro that also speaks Hebrew: MLX Kokoro for every language's voices (Pipecat's ONNX Kokoro if MLX fails),
and Hebrew Kokoro for he_shaul.

MLX Kokoro is the same model and voices on the GPU: the first sound of a reply comes in ~0.04-0.12 s instead
of ONNX's ~0.2-0.6 s (it grows with the sentence there), and it reads versions better ("v0.18.0" as
"eighteen"). It reads English through misaki and the other languages through espeak, as ONNX Kokoro does.
Hebrew Kokoro ships only as ONNX: hebrew_mlx.py converts its weights for MLX once, and reads Phonikud's IPA.
If MLX fails, that piece is spoken by ONNX (Hebrew Kokoro for Hebrew).

Hebrew Kokoro (thewh1teagle/kokoro-hebrew-nc, Kokoro-82M fine-tuned on the SASPEECH recordings: one male
voice, non-commercial licence) reads IPA, not letters. Written Hebrew leaves out most vowels and all stress,
so Phonikud adds them first (diacritics, ~0.02 s a sentence) and turns the result into IPA. On an M-series
Mac it speaks about 10x faster than real time. Both models (~300 MB each) load the first time Hebrew speaks.
"""

import asyncio
import re
import threading

import numpy as np
from loguru import logger
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame
from pipecat.audio.utils import create_stream_resampler
from pipecat.services.kokoro.tts import KOKORO_CACHE_DIR, KokoroTTSService
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language

HEBREW_VOICE = "he_shaul"
_MODEL_REPO = "thewh1teagle/kokoro-hebrew-nc"
_NIKUD_REPO = "thewh1teagle/phonikud-onnx"
# Each repo at the commit voice was checked with: a later push can't change what runs here.
_REVISIONS = {_MODEL_REPO: "07e2334fc252d7965cb8dbd503bf7565bad71228", _NIKUD_REPO: "b806189fe1fc0085b1012b7560ffb5e8ecfd72a2"}


class _Hebrew:
    """Phonikud and Hebrew Kokoro, loaded once."""

    def __init__(self):
        from huggingface_hub import hf_hub_download
        from kokoro_onnx import Kokoro
        from phonikud_onnx import Phonikud

        get = lambda repo, name: hf_hub_download(repo, name, revision=_REVISIONS[repo])
        self.kokoro = Kokoro(get(_MODEL_REPO, "kokoro.onnx"), get(_MODEL_REPO, "voices-hebrew.bin"),
                             vocab_config=get(_MODEL_REPO, "config.json"))
        self.nikud = Phonikud(get(_NIKUD_REPO, "phonikud-1.0.int8.onnx"))

    def ipa(self, text: str) -> str:
        """Hebrew through Phonikud; English words (Latin letters) said as Israelis say them (english_ipa)."""
        out, last = [], 0
        for m in LATIN.finditer(text):
            if text[last:m.start()].strip():
                out.append(self._hebrew_ipa(text[last:m.start()]))
            out.append("".join(PREFIX_SOUNDS[c] for c in (m.group(1) or "")) + english_ipa(m.group(2)))
            last = m.end()
        if text[last:].strip():
            out.append(self._hebrew_ipa(text[last:]))
        return " ".join(out)

    def _hebrew_ipa(self, text: str) -> str:
        from phonikud import phonemize

        text = with_known_words(text)
        return phonemize(keep_given_nikud(text, self.nikud.add_diacritics(text)))


# English words in a Hebrew reply ("ה-PR", "ב-commit", "Pull Request"): Phonikud reads only Hebrew, so it garbled
# them ("ommit", "ull euest") or dropped them (PR). misaki, Kokoro's English reader, gives their sounds, mapped to
# the nearest Hebrew ones: said the way Israelis say English words, in the same voice. A Hebrew prefix on one
# ("ב-commit") keeps its vowel, which Phonikud can't give a letter standing alone.
LATIN = re.compile(r"(?:(?<![א-ת])([והבכלמש]{1,3})[-־])?([A-Za-z][A-Za-z0-9.'’]*(?:[ -][A-Za-z][A-Za-z0-9.'’]*)*)")
PREFIX_SOUNDS = {"ה": "ha", "ב": "be", "ל": "le", "ו": "ve", "מ": "me", "ש": "ʃe", "כ": "ke"}
TO_HEBREW_SOUNDS = [("ɚ", "eʁ"), ("ɹ", "ʁ"), ("ɪ", "i"), ("ʊ", "u"), ("ə", "e"), ("ᵊ", "e"), ("æ", "a"), ("ɑ", "a"),
                    ("ɒ", "o"), ("ɔ", "o"), ("ʌ", "a"), ("ɛ", "e"), ("ɜ", "e"), ("A", "ej"), ("I", "aj"), ("O", "o"),
                    ("W", "aw"), ("Y", "oj"), ("θ", "t"), ("ð", "d"), ("ŋ", "n"), ("ʤ", "dʒ"), ("ʧ", "tʃ"), ("ː", ""),
                    ("ˌ", ""), ("T", "t"), ("ᵻ", "e"), ("Q", "o")]  # misaki's own letters: T a flapped t, Q British "oh"
_english = None


def english_ipa(words: str) -> str:
    """English words as an Israeli says them: misaki's sounds, each mapped to the nearest Hebrew one."""
    global _english
    if _english is None:
        from misaki import en

        _english = en.G2P(trf=False, british=False)
    sounds = _english(words)[0]
    for english, hebrew in TO_HEBREW_SOUNDS:
        sounds = sounds.replace(english, hebrew)
    return sounds


NIKUD = re.compile(r"[ְ-ׇּׁׂ]")  # vowel points, dagesh, shin/sin dots
MARKED = re.compile(r"[\u05ab\u05b0-\u05bc\u05c1\u05c2\u05c7]")  # niqqud, or Phonikud's stress mark alone


STRESS = "֫"  # Phonikud's stress mark


def _letters(word: str) -> list[str]:
    """A word as its letters, each with the marks after it (and Phonikud's '|' prefix break kept in front)."""
    out = []
    for ch in word:
        if out and not ("א" <= ch <= "ת") and ch != "|":
            out[-1] += ch
        else:
            out.append(ch)
    return out


# Words Phonikud misreads, as they should sound (niqqud, and the stress mark on the stressed syllable). Found by
# reading the talker's Hebrew through Phonikud: it says "lesˈeʃan", "hasʃˈen" and "sˈʃanim". Add a word when
# you hear voice say one wrong; a prefix (ה ו ב כ ל מ ש) in front keeps Phonikud's vowel.
KNOWN_WORDS = {
    "סשן": "סֶ\u05abשֶׁן",
    "סשנים": "סֶשֶׁנִ\u05abים",
    "פול": "פּוּ\u05abל",  # pull (request); Phonikud: "pol"
    "ריקווסט": "רִיקְוֶ\u05abסְט", "ריקוסט": "רִיקְוֶ\u05abסְט",  # request; Phonikud, after a marked "פול": "ʁikvˈust"
    "טוקן": "טּוֹ\u05abקֶן",  # Phonikud: "tokˈan". Every letter marked (dagesh on ט): a bare one takes Phonikud's vowel
    "טוקנים": "טּוֹקֶנִ\u05abים",
    "טסט": "טֶ\u05abסְט",
    "טסטים": "טֶסְטִ\u05abים",  # Phonikud: "tastˈim"
    "בראנץ'": "בְּרַ\u05abנְץ'",
}
# A prefix before a known word gets its vowel from here: Phonikud sometimes leaves it bare ("lbʁˈantʃ").
# A shva there is sounded ("le"), which Phonikud marks with a meteg (\u05bd) after it; without it, it's silent.
_PREFIX_NIKUD = {"ה": "הַ", "ו": "וְ\u05bd", "ב": "בְּ\u05bd", "כ": "כְּ\u05bd", "ל": "לְ\u05bd", "מ": "מֵ", "ש": "שֶׁ"}
_PREFIXES = "הובכלמש"


def with_known_words(text: str) -> str:
    """Each KNOWN_WORDS word, with or without prefixes, as it should sound; the rest as written."""
    def known(m):
        word = m.group(0)
        for k in range(min(3, len(word) - 1) + 1):
            if word[k:] in KNOWN_WORDS and all(c in _PREFIXES for c in word[:k]):
                return "".join(_PREFIX_NIKUD[c] for c in word[:k]) + KNOWN_WORDS[word[k:]]
        return word
    return re.sub(r"[\u05d0-\u05ea]+'?", known, text)  # a geresh ends some words: "בראנץ'"


def keep_given_nikud(given: str, guessed: str) -> str:
    """Phonikud's reading, except letters that came with nikud keep it: Phonikud overwrites what it's given
    ("הסַפָּר", the barber, comes back "הסֵּפֶר", the book), and the talker marks a word only when the sentence
    doesn't settle how to read it, often only its stem. So per letter: the talker's marks where it wrote some,
    Phonikud's elsewhere (the prefix "ה" keeps its vowel); in such a word Phonikud's stress, which belonged to
    its own reading, goes, and phonemize places it. Phonikud keeps words and letters in order, so they pair up."""
    words, out = given.split(), guessed.split()
    if not MARKED.search(given) or len(words) != len(out):
        return guessed
    merged = []
    for w, o in zip(words, out):
        if not MARKED.search(w):
            merged.append(o)
            continue
        mine, theirs = _letters(w), _letters(o)
        base = lambda p: "".join(c for c in p if "א" <= c <= "ת")
        if [base(p) for p in mine if base(p)] != [base(p) for p in theirs if base(p)]:
            merged.append(w)  # the letters don't line up: the talker's word as written
            continue
        it = iter(p for p in mine if base(p))
        word = ""
        for p in theirs:
            if not base(p):
                word += p
                continue
            m = next(it)
            if NIKUD.search(m):
                word += m  # the talker's vowels (and its stress, if it marked one)
            elif STRESS in w:
                word += p.replace(STRESS, "") + (STRESS if STRESS in m else "")  # Phonikud's vowels, the talker's stress
            else:
                word += p.replace(STRESS, "")
        merged.append(word)
    return " ".join(merged)


_hebrew: _Hebrew | None = None
_hebrew_lock = threading.Lock()


def load_hebrew() -> _Hebrew:
    """Loads Hebrew on first use (or ahead of it, when you pick Hebrew), off the event loop."""
    global _hebrew
    with _hebrew_lock:
        if _hebrew is None:
            _hebrew = _Hebrew()
            logger.info("[hebrew] Hebrew Kokoro and Phonikud loaded")
        return _hebrew


_FAST_REPO = "mlx-community/Kokoro-82M-bf16"
_fast = None
_fast_hebrew = None
_fast_lock = threading.Lock()  # one load, and one MLX generation at a time
_MAX_PHONEMES = 500  # Kokoro's context is 510 tokens; a longer piece goes to ONNX, which splits it


def load_fast_kokoro():
    """Loads MLX Kokoro on first use (or up front, at start-up), off the event loop."""
    global _fast
    with _fast_lock:
        if _fast is None:
            from mlx_audio.tts.utils import load_model

            _fast = load_model(_FAST_REPO)
            list(_fast.generate("Ready.", voice="af_heart", lang_code="a"))  # compiles it
            logger.info("[kokoro] MLX Kokoro loaded")
        return _fast


def load_fast_hebrew():
    """Hebrew Kokoro on MLX (hebrew_mlx.py: converted from its ONNX weights the first time), and Phonikud."""
    global _fast_hebrew
    load_hebrew()  # Phonikud, and the ONNX voice as the fallback
    with _fast_lock:
        if _fast_hebrew is None:
            import hebrew_mlx

            _fast_hebrew = hebrew_mlx.load_model()
            logger.info("[hebrew] Hebrew Kokoro on MLX loaded")
        return _fast_hebrew


def _speak_fast_hebrew(text: str, speed: float) -> tuple[np.ndarray, int]:
    import mlx.core as mx

    model, hebrew = load_fast_hebrew(), load_hebrew()
    ipa = hebrew.ipa(text)
    n = sum(p in model.vocab for p in ipa)
    if n > _MAX_PHONEMES:
        raise ValueError(f"{n} phonemes, more than one MLX pass takes")
    style = hebrew.kokoro.voices[HEBREW_VOICE]
    with _fast_lock:
        out = model(ipa, mx.array(style[min(n, len(style)) - 1]), speed)
        audio = np.array(getattr(out, "audio", out), dtype=np.float32).reshape(-1)
    return audio, 24000


def _speak_fast(text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
    """The whole piece at once: MLX Kokoro makes a sentence in well under 0.15 s, so streaming adds nothing."""
    if voice == HEBREW_VOICE:
        return _speak_fast_hebrew(text, speed)
    model = load_fast_kokoro()
    with _fast_lock:
        parts = [np.array(r.audio, dtype=np.float32).reshape(-1)
                 for r in model.generate(text, voice=voice, speed=speed, lang_code=voice[0])]  # af_ -> a, ef_ -> e, ...
    return np.concatenate(parts) if parts else np.zeros(0, np.float32), model.sample_rate


class KokoroAnyLanguage(KokoroTTSService):
    def __init__(self, *, settings: KokoroTTSService.Settings, lazy: bool = False, **kwargs):
        # Pipecat checks the voice against Kokoro's own voices file, which has no he_shaul: start on an
        # English voice, then switch.
        voice = settings.voice
        if lazy:
            # A first run: Kokoro's ONNX files may not be there yet (its setup fetches them, reporting to the
            # window), so skip pipecat's download and load; ONNX Kokoro, only the fallback, loads on first use.
            defaults = self.Settings(model=None, voice=None, language=Language.EN, speed=1.0)
            defaults.apply_update(settings)
            TTSService.__init__(self, push_start_frame=True, push_stop_frames=True, settings=defaults, **kwargs)
            self._resampler = create_stream_resampler()
            self._onnx = None
        else:
            if voice == HEBREW_VOICE:
                settings.voice = "af_heart"
            super().__init__(settings=settings, **kwargs)
        self._settings.voice = voice

    @property
    def _kokoro(self):
        if self._onnx is None:
            from kokoro_onnx import Kokoro

            self._onnx = Kokoro(str(KOKORO_CACHE_DIR / "kokoro-v1.0.onnx"), str(KOKORO_CACHE_DIR / "voices-v1.0.bin"))
        return self._onnx

    @_kokoro.setter
    def _kokoro(self, value):  # pipecat's own __init__ sets it
        self._onnx = value

    async def run_tts(self, text: str, context_id: str):
        # MLX first (on the GPU: first sound in ~0.04-0.12 s, not ~0.2-0.6 s); ONNX below if it fails.
        try:
            await self.start_tts_usage_metrics(text)
            samples, rate = await asyncio.to_thread(_speak_fast, text, self._settings.voice, self._settings.speed or 1.0)
            await self.stop_ttfb_metrics()
            audio = await self._resampler.resample((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes(), rate, self.sample_rate)
            yield TTSAudioRawFrame(audio=audio, sample_rate=self.sample_rate, num_channels=1, context_id=context_id)
            return
        except Exception as e:
            logger.warning(f"[kokoro] MLX Kokoro failed ({e}); speaking this with ONNX Kokoro")
        if self._settings.voice != HEBREW_VOICE:
            async for frame in super().run_tts(text, context_id):
                yield frame
            return
        try:
            await self.start_tts_usage_metrics(text)
            hebrew = await asyncio.to_thread(load_hebrew)
            ipa = await asyncio.to_thread(hebrew.ipa, text)
            logger.debug(f"[hebrew] {text!r} -> {ipa!r}")
            stream = hebrew.kokoro.create_stream(ipa, voice=HEBREW_VOICE, speed=self._settings.speed or 1.0, is_phonemes=True)
            async for samples, sample_rate in stream:
                await self.stop_ttfb_metrics()
                audio = await self._resampler.resample((samples * 32767).astype(np.int16).tobytes(), sample_rate, self.sample_rate)
                yield TTSAudioRawFrame(audio=audio, sample_rate=self.sample_rate, num_channels=1, context_id=context_id)
        except Exception as e:
            yield ErrorFrame(error=f"Hebrew voice failed: {e}")
        finally:
            await self.stop_ttfb_metrics()
