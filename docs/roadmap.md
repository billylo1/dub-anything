# Roadmap

Done so far:
- joining parts into one video (dub + original audio, EN/JA soft subtitles);
- VAD onset snapping, plus pulling window starts back to the true attack
  and window ends past the release (`edges.py`), which removed clicks at line
  starts;
- speech already in English found and transcribed as English (`draft.py`),
  so it's kept and captioned instead of read as Japanese;
- flashback echo matched: `tails.py` measures each line's reverb tail, and a
  patched-in `echo` preset provides heavy reverberation;
- PA announcements matched: a patched-in `pa` preset (reverb plus the PA's
  tone), for reverberant announcements;
- a second opinion on the transcript: ReazonSpeech and SenseVoice
  (`second.py`) flag lines where both disagree with Whisper;
- long Whisper hallucinations re-transcribed a stretch at a time, finding
  accented English by Whisper's confidence in each language;
- register-matched references (`register.py`): a second reference per
  character, calm or raised, built from their own lines and chosen per line
  by the original's delivery, with a check that it delivers;
- cleaner separation (`separate.py`): Kim's Mel-Band Roformer instead of
  htdemucs. Compare vocals and background on each new project;
- casting by speaker embeddings, voice clustering, and a show-wide casting
  registry built from lines already cast;
- voices pooled from several parts, so a character's reference can use clean
  lines from every part they appear in;
- no more first-render re-rolls: patched VoiceStudio keeps a failed render's
  takes, and `fit.py` predicts each line's length per voice before rendering;
- per-line QA with normalization, negation checks and re-rolls, plus a
  one-pass overflow report;
- 32-step synthesis;
- voice processing A/B (compare presets on each project);
- alternative Japanese recognisers for comparison with Whisper;
- per-line references (default): each line's reference ends with the actor's
  own original of it, so the take follows that line's delivery with the
  actor's accent, behind a check that the window is clean;
- fill (default): takes re-rendered at 0.8–1.25× so each ends where the actor
  stops, and `fit.py` flags English too short for the actor's speech;
- IndexTTS 2.5 with per-line emotion references, as an option
  (`--engine indextts2`);
- burned-in closed captions read by OCR (`subs.py`) and shown under each
  draft phrase: exact words, speaker names and colours, song lyrics kept out;
- the `"american"` voice: IndexTTS's native English, converted to each actor's
  timbre (`vc.py`, Chatterbox), offered per show next to the actor's own
  accented voice. How these were chosen: [research.md](research.md).

Next, roughly by expected payoff:

1. **Per-line references and fill, follow-ups** (see
   [research.md](research.md#open-questions)): more lines with clean windows
   (snapping, a transcript per voice clip for long lines, a kana-aware window
   check); matching pauses inside a line, not just its end; a variant with fill
   alone, to see how much of the gain is timing.
2. **Line edges.** `edges.py` fixes the clearest chopped starts and ends.
   Still open:
   - fragments it skips;
   - VoiceStudio's 10 ms crossfade itself: a longer or level-aware fade would
     soften any remaining edge, but that means patching VoiceStudio.

   Tune these by ear, using the lines listeners flag.
3. **Better source transcripts.** Compare recognisers on representative,
   corrected samples. `second.py` provides agreement-based review flags, and
   burned-in captions (`subs.py`) give the exact words where a recording has
   them; a Japanese-trained OCR model would misread fewer of them. Explore
   aligned word timings and importing subtitle streams.

4. **Match room acoustics** per scene. `tails.py`, `echo` and `pa` are
   starting points; compare reverb and EQ on the actual source. Effects
   change synthesis cache keys, so review new takes after adjusting them.

5. **Acting beyond the original line.** OmniVoice has no emotion control; its
   only style tag is `whisper`. Per-line references carry the actor's delivery
   where a line's window is clean. Elsewhere the character's reference, or a
   calm/raised one from `register.py`, sets it. The `"american"` voice
   (IndexTTS, then conversion to the actor) acts well but loses some of its
   intonation in conversion, and its takes end short of the actor more often:
   a converter that keeps the pitch contour, and English sized for IndexTTS's
   faster pace (`fit.py` predicts OmniVoice's), would close both gaps.
6. **Lip sync** (LatentSync / MuseTalk). Feasible on a decent NVIDIA GPU.
7. **Other language pairs.** `dub.py prep` and `draft.py` assume a Japanese
   source and English target.
8. **Upstream to VoiceStudio:**
   - the fixes in `patches/voicestudio-local-fixes.patch`, the `echo` and `pa`
     presets, and per-segment voice and emotion references in dubs;
   - a Roformer separator as an option next to htdemucs (dubrama swaps the
     stems in the job folder with `separate.py`);
   - free-text `direction`/`instruct` aborting OmniVoice dubs.
