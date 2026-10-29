"""Kokoro, the voice the talker speaks with, loaded directly (the tests make test speech with it)."""

from kokoro_onnx import Kokoro
from pipecat.services.kokoro.tts import KOKORO_CACHE_DIR, _ensure_model_files


def kokoro() -> Kokoro:
    # Same files Pipecat's KokoroTTSService downloads and uses.
    model, voices = KOKORO_CACHE_DIR / "kokoro-v1.0.onnx", KOKORO_CACHE_DIR / "voices-v1.0.bin"
    _ensure_model_files(model, voices)
    return Kokoro(str(model), str(voices))
