"""Every voice speaks through MLX Kokoro, fast and whole (no model calls, no mic).

MLX Kokoro is the same model and voices as ONNX Kokoro, on the GPU. Checks: a short piece's audio is ready in
well under ONNX's ~0.2 s; each menu voice in each language speaks through it, about as long as ONNX Kokoro says
the same sentence (nothing cut off). Hebrew: its ONNX weights convert from scratch (as on a new machine), it
speaks fast, about as long as Hebrew Kokoro on ONNX, and Hebrew Whisper hears it word for word.
"""

import _root  # noqa: F401  (the repo root on the import path)
import asyncio
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from kokoro_onnx import Kokoro
from loguru import logger
from pipecat.services.kokoro.tts import KOKORO_CACHE_DIR

import hebrew_mlx
import hebrew_voice
import settings
from hebrew_voice import HEBREW_VOICE, _speak_fast, load_fast_kokoro, load_hebrew

SAID = {
    "en": "You're welcome. The session is on step two of six, looking for the cause.",
    "es": "La sesión está en el paso dos de seis, buscando la causa.",
    "fr": "La session en est à l'étape deux sur six, elle cherche la cause.",
    "it": "La sessione è al passo due di sei, sta cercando la causa.",
    "pt": "A sessão está no passo dois de seis, procurando a causa.",
    "hi": "सत्र छह में से दूसरे चरण पर है, कारण ढूंढ रहा है।",
}
results = []


def ok(cond, msg):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")


async def onnx_secs(onnx: Kokoro, text: str, voice: str, lang: str) -> float:
    n, rate = 0, 24000
    async for samples, rate in onnx.create_stream(text, voice=voice, lang=lang):
        n += len(samples)
    return n / rate


def main():
    logger.remove()
    load_fast_kokoro()
    onnx = Kokoro(str(KOKORO_CACHE_DIR / "kokoro-v1.0.onnx"), str(KOKORO_CACHE_DIR / "voices-v1.0.bin"))

    _speak_fast("Warm up.", "af_heart", 1.0)
    took = min(_timed("Tokyo runs on Japan Standard Time,") for _ in range(3))
    ok(took < 0.15, f"a short piece's audio is ready in {took * 1000:.0f} ms (ONNX: ~300 ms)")

    hebrew_checks()
    for code, label, _, kokoro_lang, _ in settings.LANGUAGES:
        if code == "he":
            continue
        for voice, _ in settings.voices(code):
            audio, rate = _speak_fast(SAID[code], voice, 1.0)
            secs, loud = len(audio) / rate, float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
            ref = asyncio.run(onnx_secs(onnx, SAID[code], voice, kokoro_lang))
            ok(loud > 0.01 and 0.75 * ref < secs < 1.35 * ref,
               f"{label} {voice}: {secs:.1f} s of speech (ONNX: {ref:.1f} s)")

    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


def hebrew_checks():
    import mlx_whisper
    from scipy.signal import resample_poly

    line = "הסשן סיים את השלב השני מתוך שישה, והוא מחפש עכשיו את הסיבה לשגיאה."
    with tempfile.TemporaryDirectory() as tmp:
        hebrew_mlx.CONVERTED = Path(tmp)  # converts from scratch, as on a new machine
        hebrew_voice._fast_hebrew = None
        _speak_fast("שלום.", HEBREW_VOICE, 1.0)
        ok((Path(tmp) / "kokoro-hebrew.safetensors").exists(), "Hebrew Kokoro's ONNX weights convert for MLX")
        t = time.monotonic()
        audio, rate = _speak_fast(line, HEBREW_VOICE, 1.0)
        took = time.monotonic() - t
    onnx_audio, onnx_rate = load_hebrew().kokoro.create(load_hebrew().ipa(line), voice=HEBREW_VOICE, is_phonemes=True)
    secs, ref = len(audio) / rate, len(onnx_audio) / onnx_rate
    ok(took < 0.4 and 0.85 * ref < secs < 1.15 * ref, f"Hebrew on MLX: {secs:.1f} s of speech in {took * 1000:.0f} ms (ONNX: {ref:.1f} s)")
    heard = mlx_whisper.transcribe(resample_poly(audio, 16000, rate).astype(np.float32),
                                   path_or_hf_repo=settings.language("he")[4], language="he")["text"]
    words = lambda t: re.sub(r"[^\u05d0-\u05ea ]", "", t).split()
    ok(words(heard) == words(line), f"Hebrew Whisper hears it word for word: {heard.strip()}")


def _timed(text: str) -> float:
    t = time.monotonic()
    _speak_fast(text, "af_heart", 1.0)
    return time.monotonic() - t


main()
