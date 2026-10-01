# Roadmap

## Where it stands

dubrama dubs Japanese video into English end to end:

- **Transcript and cast.** Parts are separated with Mel-Band Roformer and
  transcribed by Whisper, with two other recognisers as a second opinion and
  burned-in captions read where a recording has them. Lines are cast by
  speaker embeddings across the show.
- **Voice and timing.** The actor's own voice: OmniVoice clones each actor,
  prompted with their delivery of each line (per-line references). Or American:
  IndexTTS converted to the actor's timbre. Either way, each take is re-timed
  to end where the actor stops (fill), and `fit.py` predicts each voice's
  lengths before the first render.
- **Checks and packaging.** Every line is transcribed back and checked. Parts
  are assembled into one video with the dub, the original audio, and English
  and Japanese subtitles.

How each piece was chosen, and the measurements behind it, are in
[research.md](research.md). The tools are listed in
[directing.md](directing.md#tools).

## Next, by payoff for what the listener hears

1. **Steadier short lines.** Lines under about 1.5 s cause most overflows, fill
   re-renders and garbled takes; one-word lines and stutters are the worst,
   and conversion makes them worse still. A re-roll no longer loses a better
   take (`lineqa.py --reroll` keeps each one and puts the best back). Next:
   render two or three takes of a short line up front and keep the one that
   fits and transcribes cleanly, instead of re-rolling round after round.
2. **More lines on their own reference, and pauses inside lines.** In
   overlapping dialogue many lines fall back to the character's reference.
   Cutting a reference at the dip between two voices (`dips`) and snapping
   touching windows to it recovered about a third of the run-on lines in fast
   argument, though in a blind listen the recovered lines sounded about the
   same as on the character's reference (research.md, finding 14). So press on
   only where fallbacks are frequent and heard. That could be reduced further by:
   - a transcript per voice clip, for long lines;
   - a kana-aware window check (katakana already reads as hiragana; kanji
     against kana still fails a short line, and needs a reading dictionary).

   Fill aligns only where a line ends, so an actor who pauses mid-sentence
   still gets continuous English.
3. **Captions, second pass.** A Japanese-trained OCR model, which would misread
   fewer kana and kanji. Turn caption colours and bracketed names into speaker
   suggestions in `draft.py`.
4. **Keep the words and the intonation through conversion.** The American
   voice's converter garbles about one line in twelve that IndexTTS said
   right; converting again fixes about half, and the rest need fresh takes
   (research.md, finding 15). It also re-generates part of the pitch contour.
   A converter that kept both while still gaining likeness would keep
   IndexTTS's acting; Seed-VC's pitch-preserving model kept the contour but
   gained no likeness.
5. **By ear, as listeners flag them:**
   - line edges: fragments `edges.py` skips, and VoiceStudio's 10 ms
     crossfade;
   - room acoustics per scene, starting from `tails.py` and the `echo` and
     `pa` presets.

## Later

- **Lip sync** (LatentSync / MuseTalk); feasible on a decent NVIDIA GPU.
- **Other language pairs.** `dub.py prep` and `draft.py` assume a Japanese
  source and an English target.
- **Upstream to VoiceStudio:**
  - the fixes in `patches/voicestudio-local-fixes.patch`, the `echo` and `pa`
    presets, and per-segment voice and emotion references in dubs;
  - a Roformer separator as an option next to htdemucs;
  - a fix for free-text `direction`/`instruct` aborting OmniVoice dubs.
