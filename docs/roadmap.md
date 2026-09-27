# Roadmap

Ideas not built yet, roughly by expected payoff.

1. **Deliverable packaging.** Join dubbed parts back into one video with the dub
   and the original language as separate audio tracks, plus soft subtitles in
   both languages.
2. **Automate the mechanical directing steps.** Onset snapping (VAD), casting
   by speaker embeddings against a recurring-character registry, per-line QA of
   rendered clips, and auto-shortening of overflowing lines.
3. **Per-line acting direction** (emotion, whisper, shout) if the engine supports
   it for cloned voices.
4. **Engine and quality A/B tests:** OmniVoice at more diffusion steps, IndexTTS 2.5
   (emotion control).
5. **Match room acoustics** per scene (reverb/EQ) so voices sit in the space.
6. **Cleaner separation** (BS-Roformer / Mel-Band Roformer vs Demucs).
   Compare residual speech in the background and music in the vocal stem
   before choosing a separator for a new project.

7. **Better source transcripts:** a Japanese-specialized ASR (kotoba-whisper,
   SenseVoice), WhisperX aligned word timings, or official subtitles (VoiceStudio
   can import SRT).
8. **Lip sync** (LatentSync / MuseTalk). Feasible on a decent NVIDIA GPU.
9. **Other language pairs:** `dub.py prep` currently hard-codes Japanese source
   and English target.
10. **Upstream the two VoiceStudio fixes** in `voicestudio-local-fixes.patch`.
