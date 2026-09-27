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
3. **Line edges.** `edges.py` fixes the clearest chopped starts. Still open:
   - fragments it skips;
   - window ends where the original voice is still sounding;
   - VoiceStudio's 10 ms crossfade itself: a longer or level-aware fade would
     soften any remaining edge, but that means patching VoiceStudio.

   Tune these by ear, using the lines listeners flag.
4. **Better source transcripts.** Only kotoba-whisper has been tried, and it
   lost. Still untried: SenseVoice, WhisperX aligned word timings, and importing
   official subtitles when a show has them (VoiceStudio can import SRT). That
   could remove most mishearing fixes.
5. **Match room acoustics** per scene (reverb/EQ) so voices sit in the space.
   VoiceStudio's only reverb is a fixed amount in its `cinematic` and `warm` presets.
6. **Cleaner separation** (BS-Roformer / Mel-Band Roformer vs Demucs).
   Compare residual speech in the background and music in the vocal stem
   before choosing a separator for a new project.

7. **Acting direction.** OmniVoice has no emotion control; its only style tag is
   `whisper`. IndexTTS 2.5 (emotion control) is the engine to try.
8. **Lip sync** (LatentSync / MuseTalk). Feasible on a decent NVIDIA GPU.
9. **Other language pairs.** `dub.py prep` and `draft.py` assume a Japanese
   source and English target.
10. **Upstream to VoiceStudio:**
   - the two fixes in `voicestudio-local-fixes.patch`;
   - free-text `direction`/`instruct` aborting OmniVoice dubs;
   - `regen_only` being ignored before a job's first success.
