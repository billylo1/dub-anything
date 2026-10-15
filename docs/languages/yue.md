# Cantonese

What differs when Cantonese is the target. Read beside
[directing.md](../directing.md) and [zh.md](zh.md). The code is
`scripts/languages/yue.py`.

## As the target: English → Cantonese

- **Validation.** A short Windows dub has run end to end in both reference modes:
  preparation, independent recognition, separation, actor cloning, fitting,
  per-line QA, signs, assembly and verified delivery. Speech errors remain in
  both variants; a Cantonese listener still needs to judge intelligibility,
  accent and voice likeness. This run does not validate macOS hardware.
- **The job.** `scripts/dub_anything.py new <video> --source en --target yue --voices actor`,
  or `scripts/dub.py prep <video> --source en --target yue` by hand. Each part records
  `en` → `yue`; original dialogue uses `en` and the dub uses `text` (Cantonese).
  Delivery includes a Cantonese dub track tagged `yue` and dual subtitles (`.yue.srt` and `.en.srt`).
- **Engine.** k2-fsa/OmniVoice natively supports Cantonese zero-shot voice cloning with
  `language: "Cantonese"` (`LANG_ID: "yue"`).
- **Per-line references.** `("en", "yue")` uses `line_ref: True`.
  The Windows comparison followed the actors' pitch and intensity more closely
  with them, with slightly better measured voice likeness and similar timing.
  Without them, estimated audio quality was better. Neither mode solved the
  pronunciation problems, so retain this starting default pending a listener's
  comparison ([research.md](../research.md), finding 21).
- **Translate and fit.** Spoken Cantonese is concise and rhythmic. Han characters are weighted
  3 units each by OmniVoice (comparable to ~1 Latin syllable). Use `fit.py` before rendering
  for a length estimate, then check the rendered takes too: a later QA reroll
  can overflow a window that an earlier take fitted. Shorten the text or widen
  into a genuine pause, and recheck before delivery.
- **QA.** `lineqa.py` uses Whisper large-v3 with `language="yue"`. `yue.py`'s
  `compare` checks character match, written or digit-form numbers, and Cantonese
  negation markers (`唔`, `冇`, `咪`, `未`, etc.). Traditional characters are
  preserved, not converted to simplified characters. Colloquial implied number
  units and idioms still need the listener's judgement. Common written variants
  such as `依家`/`而家`, pronouns ending in `地`/`哋`, and `啊`/`呀` compare equally;
  unrelated homophones remain reviewable. `係咪` is a question, while standalone
  `咪` remains a negation check. Recheck saved takes using the current QA rules.
- **Language detection.** In the Windows comparison Whisper usually labelled
  the generated speech `zh`, with some takes labelled English, and rarely
  selected `yue` in either mode. Its `P(yue)` alone is not a fluency verdict.
  Read the per-line transcripts, check an independent recognizer, and have a
  Cantonese listener judge names, short replies and heavily stylized voices.
- **Subtitles.** Long Cantonese transcript cues are broken near their middle at punctuation.
  Styled subtitles use `PingFang HK` on macOS and `Microsoft JhengHei` on Windows.
