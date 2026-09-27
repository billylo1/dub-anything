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
- alternative Japanese recognisers for comparison with Whisper.

Next, roughly by expected payoff:

1. **Line edges.** `edges.py` fixes the clearest chopped starts and ends.
   Still open:
   - fragments it skips;
   - VoiceStudio's 10 ms crossfade itself: a longer or level-aware fade would
     soften any remaining edge, but that means patching VoiceStudio.

   Tune these by ear, using the lines listeners flag.
2. **Better source transcripts.** Compare recognisers on representative,
   corrected samples. `second.py` provides agreement-based review flags.
   Explore aligned word timings and importing source subtitles.

3. **Match room acoustics** per scene. `tails.py`, `echo` and `pa` are
   starting points; compare reverb and EQ on the actual source. Effects
   change synthesis cache keys, so review new takes after adjusting them.

4. **Acting direction.** OmniVoice has no emotion control; its only style tag is
   `whisper`. Register is now matched by choosing the reference
   (`register.py`), but that needs 5 s of clean speech in each register, which
   short roles lack, and it can't do emotion beyond calm vs raised (anger vs
   joy, tears). IndexTTS 2.5 (emotion control) is the engine to try.
5. **Lip sync** (LatentSync / MuseTalk). Feasible on a decent NVIDIA GPU.
6. **Other language pairs.** `dub.py prep` and `draft.py` assume a Japanese
   source and English target.
7. **Upstream to VoiceStudio:**
   - the fixes in `voicestudio-local-fixes.patch`, and the `echo` and `pa`
     presets;
   - a Roformer separator as an option next to htdemucs (dubrama swaps the
     stems in the job folder with `separate.py`);
   - free-text `direction`/`instruct` aborting OmniVoice dubs.
