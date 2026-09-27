# Roadmap

Done so far:
- joining parts into one video (dub + original audio, EN/JA soft subtitles);
- VAD onset snapping;
- casting by speaker embeddings and voice clustering;
- per-line QA;
- 32-step synthesis.

Next, roughly by expected payoff:

1. **Voice processing A/B.** Compare `--effect broadcast` (the default:
   compression, presence EQ, -2 dBFS normalize) with `raw` (the model's output
   level-matched to the actor), and choose a default by ear.
2. **Show-wide cast registry.** A voice built from one part serves the whole
   show. Let `voices.json` take clips from several parts, and let `draft.py`
   keep a recurring character's name across episodes.
3. **Auto-fix the mechanical failures.**
   - Shorten or re-roll lines that overflow.
   - Re-roll takes that `lineqa.py` flags (a take is random, and a re-roll often fixes it).
   - Warn when a script line ends in "…".
4. **Match room acoustics** per scene (reverb/EQ) so voices sit in the space.
   VoiceStudio's only reverb is a fixed amount in its `cinematic` and `warm` presets.
5. **Cleaner separation** (BS-Roformer / Mel-Band Roformer vs Demucs).
   Compare residual speech in the background and music in the vocal stem
   before choosing a separator for a new project.

6. **Better source transcripts:** a Japanese-specialized ASR (kotoba-whisper,
   SenseVoice), WhisperX aligned word timings, or official subtitles.
7. **Acting direction.** OmniVoice has no emotion control; its only style tag is
   `whisper`. IndexTTS 2.5 (emotion control) is the engine to try.
8. **Lip sync** (LatentSync / MuseTalk). Feasible on a decent NVIDIA GPU.
9. **Other language pairs.** `dub.py prep` and `draft.py` assume a Japanese
   source and English target.
10. **Upstream the two VoiceStudio fixes** in `voicestudio-local-fixes.patch`, and
    report that free-text `direction`/`instruct` aborts OmniVoice dubs.
