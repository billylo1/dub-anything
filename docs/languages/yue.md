# Cantonese

What differs when Cantonese is the target. Read beside
[directing.md](../directing.md) and [zh.md](zh.md). The code is
`scripts/languages/yue.py`.

## As the target: English → Cantonese

- **Validation.** Unit tests cover the module and QA checks. An end-to-end short
  dub and a measured comparison with and without per-line references remain to
  be done.
- **The job.** `scripts/dub_anything.py new <video> --source en --target yue --voices actor`,
  or `scripts/dub.py prep <video> --source en --target yue` by hand. Each part records
  `en` → `yue`; original dialogue uses `en` and the dub uses `text` (Cantonese).
  Delivery includes a Cantonese dub track tagged `yue` and dual subtitles (`.yue.srt` and `.en.srt`).
- **Engine.** k2-fsa/OmniVoice natively supports Cantonese zero-shot voice cloning with
  `language: "Cantonese"` (`LANG_ID: "yue"`).
- **Per-line references.** `("en", "yue")` uses `line_ref: True`.
  Compare intelligibility, timing and voice likeness with references disabled
  on a short clip before directing a long job; this setting has not been measured.
- **Translate and fit.** Spoken Cantonese is concise and rhythmic. Han characters are weighted
  3 units each by OmniVoice (comparable to ~1 Latin syllable). Use `fit.py` before rendering
  to ensure the Cantonese line fits the actor's window without `OVER?`.
- **QA.** `lineqa.py` uses Whisper large-v3 with `language="yue"`. `yue.py`'s
  `compare` checks character match, written or digit-form numbers, and Cantonese
  negation markers (`唔`, `冇`, `咪`, `未`, etc.). Traditional characters are
  preserved, not converted to simplified characters. Colloquial implied number
  units and idioms still need the listener's judgement.
- **Subtitles.** Long Cantonese transcript cues are broken near their middle at punctuation.
  Styled subtitles use `PingFang HK` on macOS and `Microsoft JhengHei` on Windows.
