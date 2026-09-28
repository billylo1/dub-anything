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
8. **Fill carries the timing, per-line references carry the delivery.** On a
   new show, one part with range was rendered three ways from the same script:
   plain, fill only, and fill with per-line references. Fill alone halved the
   distance between the take's end and the actor's, as much as the full
   default did. But its delivery followed the originals no better than the
   plain dub: the rank correlation stayed near zero. Only per-line references
   made it clearly positive. Likeness, estimated quality and accent were about
   the same in all three. So the preference in finding 7 isn't timing alone. The
   plain variant also needed the old chores: five lines overflowed at natural
   pace and had to be reworded or widened, and re-rolls changed short takes'
   lengths enough to overflow again. Fill absorbed all of that.
9. **OmniVoice decides a take's length by rule.** Its length is the
   reference's duration scaled by the weight of the English against the weight
   of the reference's transcript, with a stretch for anything under 2 s (see
   [directing.md](directing.md#3-translate-and-time)). With per-line
   references each line's own original enters the rule, so pace varies from
   line to line. `fit.py` used to estimate lengths from each voice's average
   rate on lines already rendered. That needs renders first, and it can't
   follow per-line pace: on a new show's first parts, nine in ten of its
   estimates were within 0.6 s, and they were biased long. Now it applies the
   rule itself, to the reference each line will get: over about 1,000 takes,
   typically within 0.2 s, nine in ten within 0.4 s, with no bias and no
   renders needed. That is about as close as two takes of one line are.
   Takes speak only about half of the stretch given to short lines. A take
   asked for at another speed changes length by about speed^0.7, not in
   proportion: slowed, the model often speaks at its own pace and leaves
   silence that is trimmed. On the lines where slowing mattered most, it added
   almost nothing. So `fit.py` flags `SHORT?` against the natural pace and
   gives fill no credit. Before, it caught three of the lines that ended at the
   0.8× floor on a new show's first render; now it catches thirteen of
   seventeen.
10. **Windows written to end in pauses keep most lines on their own
    reference.** When every window was ended in a pause while writing the
    script, rather than snapped afterwards, about nine lines in ten passed the
    window check on a dialogue-heavy show, against about seven in ten before.
11. **Choosing each line's speed from the prediction saves renders, not
    timing.** On a third show, three parts of an ensemble scene (about 110
    lines) were rendered twice from the same script, each in its own job. One
    used the current fill: natural pace first, then re-timing. The other
    rendered each new line at the speed the length prediction asked for, and
    corrected afterwards by speed^0.7. The prediction put about two lines in
    three within fill's tolerance on the first pass, where natural pace managed
    about one in three. Fill passes re-rendered about half as many lines, and
    the whole render made about 15% fewer takes. Everything measured on the
    result was the same:
    the distance between each take's end and the actor's, lines ending late,
    `lineqa.py` flags, delivery, likeness and estimated quality. Wall time saved
    little: the first pass dominates, and every run has fixed costs. It also
    had a cost. Short takes vary so much from roll to roll that the speed^0.7
    correction over-steered them. One short line swung from well under the
    actor's time at 1.25× to far over it at 0.8×, and three times as many
    lines ended at the 0.8× floor. So fill still
    starts at natural pace; the prediction is a switch (`predict_speed`). Both
    variants also stopped on lines still overflowing at 1.25×, sometimes by a
    hundredth of a second, because fill never re-rendered a take already at
    its fastest speed. Now such a take gets a fresh roll.

## What the pipeline does now

`dub.py generate` renders new parts with per-line references and fill by
default, recorded in each script's `render` block (see
[directing.md](directing.md#4-render)). `fit.py` predicts each line's length with
OmniVoice's own rule before the first render. It flags `SHORT?` lines, whose
English is too short for the actor's speech at natural pace, so the director
writes fuller text before rendering. `register.py`'s calm/raised
references are now a fallback for characters whose lines can't use their own
original.

## Open questions

- **Steadier short takes.** Most fill passes, overflows and re-rolls come
  from lines under about 1.5 s, whose takes vary most from roll to roll (see
  finding 11). Rendering several takes of a short line and keeping the one
  that fits might settle them in one pass.
- **Pauses inside a line.** Fill aligns where a line ends. An actor who pauses
  mid-sentence still gets continuous English.
- **Coverage of per-line references.** Snapping windows to pauses helps. Long
  lines hit OmniVoice's 20 s reference limit next to the voice's clips; using
  fewer clips needs a transcript per clip. The window check compares
  transcripts as raw text, so kanji against kana counts as a difference, and it
  only rejects gross mismatches; a kana-aware comparison could be stricter.
- **IndexTTS's acting with the actor's voice.** A voice conversion pass could
  bring back the likeness, but not the accent.
