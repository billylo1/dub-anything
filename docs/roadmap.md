# Roadmap

Done so far:
- joining parts into one video (dub + original audio, EN/JA soft subtitles);
- VAD onset snapping;
- casting by speaker embeddings, voice clustering, and a show-wide casting
  registry built from lines already cast;
- per-line QA with normalization, negation checks and re-rolls, plus a
  one-pass overflow report;
- 32-step synthesis;
- voice processing A/B (compare presets on each project);
- alternative Japanese recognisers for comparison with Whisper.

Next, roughly by expected payoff:

1. **Voices from several parts.** A voice's reference clips must currently come
   from one clip (`"from"`). Let a character's reference draw on clean lines
   from any directed part, so bit parts with little speech per part can still
   get a good voice. `draft.py` already pools lines this way for casting.
2. **Fewer first-render re-rolls.** Until a job's first successful render,
   VoiceStudio re-synthesizes everything on each attempt. Pre-check lengths
   before the first render (estimate from characters per second per voice), or
   patch VoiceStudio to honour `regen_only` on uncompleted jobs.
3. **Match room acoustics** per scene. `tails.py`, `echo` and `pa` are
   starting points; compare reverb and EQ on the actual source. Effects
   change synthesis cache keys, so review new takes after adjusting them.

4. **Cleaner separation** (BS-Roformer / Mel-Band Roformer vs Demucs).
   Compare residual speech in the background and music in the vocal stem
   before choosing a separator for a new project.

5. **Acting direction.** OmniVoice has no emotion control; its only style tag is
   `whisper`. IndexTTS 2.5 (emotion control) is the engine to try.
6. **Lip sync** (LatentSync / MuseTalk). Feasible on a decent NVIDIA GPU.
7. **Other language pairs.** `dub.py prep` and `draft.py` assume a Japanese
   source and English target.
8. **Upstream to VoiceStudio:**
   - the two fixes in `voicestudio-local-fixes.patch`;
   - free-text `direction`/`instruct` aborting OmniVoice dubs;
   - `regen_only` being ignored before a job's first success.
