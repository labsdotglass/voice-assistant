# Hebrew

Hebrew is experimental: in the orb's Language menu it sits at the bottom, below a divider.

- **Hearing:** ivrit.ai's Hebrew Whisper (v3 turbo, 8-bit MLX) instead of Whisper large-v3.
- **Voice:** Hebrew Kokoro, one male voice. **Its model is for non-commercial use only** (it's trained on [SASPEECH](https://www.openslr.org/134/)). It ships only as ONNX; `hebrew_mlx.py` converts its weights for MLX the first time Hebrew is used (a few seconds, kept in `~/.voice-assistant/kokoro-hebrew/` and never published).
- **The first switch to Hebrew** downloads its models, about 2.8 GB.

## Reading

Written Hebrew leaves out most vowels, so Kokoro needs them added:

- **Phonikud** guesses each word's vowels and stress. `hebrew_voice.py` keeps the vowel marks (niqqud) the talker writes, letter by letter, over Phonikud's guesses.
- **`KNOWN_WORDS`** lists words Phonikud misreads (it read "סשן" wrong), read as listed with any prefix. Add a word there when you hear voice say one wrong.
- **English words** in a Hebrew reply ("ה-PR", "ב-commit") are said the way Israelis say them: misaki's English sounds mapped to the nearest Hebrew ones, in the same voice.
- **Tech terms:** the talker writes them in Hebrew letters, which it does reliably; on screen they show in English (`hebrew_terms.py`: "הקומיט" → "ה-commit"), while the voice says them from the Hebrew. Add a term to `TERMS` when you see one left in Hebrew.
- **Gendered "you":** once the talker knows your gender, it marks "you" words that are spelled the same for a man and a woman, so voice says them in yours.
- **Spoken, not written, Hebrew:** the talker answers in everyday spoken Israeli Hebrew, even when the session writes formally in English (`tests/test_spoken_hebrew.py`, run by hand: Haiku passes it about half the time).
