# Cantonese

What differs when Cantonese is the target. Read beside
[directing.md](../directing.md) and [zh.md](zh.md). The code is
`scripts/languages/yue.py`.

## As the target: English → Cantonese

- **The job.** `scripts/dub_anything.py new <video> --source en --target yue --voices actor`,
  or `scripts/dub.py prep <video> --source en --target yue` by hand. Each part records
  `en` → `yue`; original dialogue uses `en` and the dub uses `text` (Cantonese).
  Delivery includes a Cantonese dub track tagged `yue` and dual subtitles (`.yue.srt` and `.en.srt`).
- **Engine.** k2-fsa/OmniVoice natively supports Cantonese zero-shot voice cloning with
  `language: "Cantonese"` (`LANG_ID: "yue"`).
- **Per-line references.** `("en", "yue")` uses `line_ref: True` to match pitch and pace
  from the original actor's delivery.
- **Translate and fit.** Spoken Cantonese is concise and rhythmic. Han characters are weighted
  3 units each by OmniVoice (comparable to ~1 Latin syllable). Use `fit.py` before rendering
  to ensure the Cantonese line fits the actor's window without `OVER?`.
- **QA.** Per-line transcription with Whisper (`language="zh"`) or SenseVoice. `yue.py`'s
  `compare` normalizes traditional characters and checks character match, numbers, and
  Cantonese negation markers (`唔`, `冇`, `咪`, `未`, etc.).
- **Subtitles.** Long Cantonese transcript cues are broken near their middle at punctuation.
  Styled subtitles use `PingFang HK` on macOS and `Microsoft JhengHei` on Windows.
