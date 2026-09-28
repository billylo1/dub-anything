# Research notes: making the clone act like the actor

What was measured and tried to get from "the right voice saying the right words"
to "the actor's performance, in English", and why the pipeline now works as it
does. The trials used the Japanese→English dramas and travel shows this toolkit
was built on. Their content and exact numbers stay in the local `work/NOTES.md`.

## How the variants were compared

Each idea became a **variant**: the same part rendered with the same script
(identical English), in a separate VoiceStudio job, so approved takes never
moved. Every variant got the same treatment: overflowing lines re-rolled, then
a small overflow allowance if needed, then `lineqa.py` re-rolls. Variants were
rendered twice where it mattered, because two renders of the same settings
differ. A listener then compared the parts by ear, and their verdict decided.

The instruments, all run against the actor's own original line:

| What | How |
|---|---|
| Delivery | Per line: pitch (semitones), vocal effort (spectral tilt, 1–4 kHz against 0.1–1 kHz) and loudness, combined relative to that actor's own lines (`register.py`'s measure). Rank correlation between each take and its original, per character. |
| Likeness | WeSpeaker similarity of each take to the actor's own lines, and whether the nearest actor is the right one. |
| Timing | The actor's last sound in each window (Silero VAD on the separated vocals) against where the take ends. "Dead air": the actor still talking after the English stopped. "Overrun": the reverse. |
| Quality | torchaudio's SQUIM (reference-free PESQ and SI-SDR estimates), and the level of the quietest frames inside the speech (noise between words). |
| Accent | Whisper's confidence that a take is English (native speech scores near 1; a clone carrying the actor's accent scores well below), plus an English-accent classifier. |
| Intelligibility | `lineqa.py`. |

Pick test material with range. The first trial scene was quiet, so every
variant followed the actor about equally and the test showed little. A scene
whose delivery swings from murmurs to shouts separated the variants clearly.
`register.py`'s arousal measure finds such scenes: take the spread of the
originals per part.

## Findings

1. **Likeness was not the gap.** Clones were identified as their own actor
   about as reliably as the actor's original lines were.
2. **Delivery was.** With one reference per character, the takes' intensity
   barely followed the originals. OmniVoice copies the delivery of its
   reference, so every line got the reference's delivery. Calm/raised
   references (`register.py`) helped the characters who had enough clean speech
   for them, and only as far as two buckets can.
3. **Timing was the other gap.** Concise English at the clone's natural pace
   ended well before the actor stopped on a large share of lines, and no check
   flagged it: `fit.py` only warned about lines running long.
4. **Cleaner separation cleans the clones.** References cut from Roformer
   vocals gave takes with less noise between words and better estimated
   quality than htdemucs references, with no change in delivery.
5. **IndexTTS 2.5 with an emotion reference acts best, but loses the actor.**
   With each line's original as its emotion reference, it followed the actors'
   delivery most closely of everything tried, on a scene with range; on a quiet
   scene it had nothing to add. The reference also carries the actor's pacing:
   without it the takes ran long and overflowed. But it speaks its own native
   English instead of the actor's accent, its likeness is lower, and it is
   several times slower. The listener found the actor's own accent more
   authentic, so it stays an option (`--engine indextts2 --emo-alpha`), not the
   default.
6. **A per-line reference does most of that within OmniVoice.** Each line's
   reference became the voice's clips followed by the actor's original of that
   line. The takes followed the actor's delivery clearly better than with the
   character's usual reference, most for characters the calm/raised references
   hadn't helped, while keeping accent and likeness. The risk is leakage: a
   window with another speaker in it, or cut mid-sound, produced gibberish on
   every take. A check on each window (speech running past its end, Whisper
   hearing something else, the voice nearer another character) now excludes
   those. In overlapping dialogue it excludes many lines, which then keep the
   character's reference.
7. **Filling the actor's time is what the listener heard most.** Re-rendering
   takes at 0.8–1.25× so each ends where the actor stops cut the dead air
   substantially, and cut the overruns too. The listener preferred the
   variant with per-line references and fill over every other. They did not
   prefer per-line references without fill over the plain dub. The rhythm of
   the exchange — lines taking the time the actor takes, turns landing where
   they did in the original — mattered at least as much as the acting. Most
   re-timed lines were slowed, and many hit the 0.8× floor: the English was
   written much shorter than the original speech.

## What the pipeline does now

`dub.py generate` renders new parts with per-line references and fill by
default, recorded in each script's `render` block (see
[directing.md](directing.md#4-render)). `fit.py` flags `SHORT?` lines, whose
English is too short for the actor's speech even at the slowest fill, so the
director writes fuller text before rendering. `register.py`'s calm/raised
references are now a fallback for characters whose lines can't use their own
original.

## Open questions

- **Is it the timing alone?** A variant with fill but without per-line
  references would show how much of the preference is timing.
- **Pauses inside a line.** Fill aligns where a line ends. An actor who pauses
  mid-sentence still gets continuous English.
- **Coverage of per-line references.** Snapping windows to pauses helps. Long
  lines hit OmniVoice's 20 s reference limit next to the voice's clips; using
  fewer clips needs a transcript per clip. The window check compares
  transcripts as raw text, so kanji against kana counts as a difference, and it
  only rejects gross mismatches; a kana-aware comparison could be stricter.
- **IndexTTS's acting with the actor's voice.** A voice conversion pass could
  bring back the likeness, but not the accent.
