# Directing a dub

The machines separate, transcribe and synthesize. Directing is everything
between the transcript and the render: what was really said, who said it, what
it means in English, and exactly when it is spoken. This guide is the workflow
that produced good results, plus the traps found along the way.

Run everything from the repo folder with VoiceStudio's Python
(`$py = "C:\git\VoiceStudio\.venv\Scripts\python.exe"`), with the backend up
(see [setup.md](setup.md)).

## Files

| Path | What |
|---|---|
| `videos/` | Source media (git-ignored). Split parts go in `videos/<stem>-parts/` and dubs in `.../dubbed/` |
| `work/voices.json` | The cast: per character, clean clips of the actor plus their exact transcript |
| `work/<clip>/script.json` | The directed dub, the real work product |
| `work/<clip>/segments.json`, `ja.srt` | Raw machine transcript and the VoiceStudio job id (`prep` overwrites them) |
| `work/NOTES.md` | Per-show notes: story, characters, running jokes, word choices |

`work/` is git-ignored: it holds transcripts of the source and references to
real voices.

### voices.json

```json
{
  "speaker_1": {
    "from": "episode-01",
    "clips": [[10.25, 13.95], [46.75, 48.65], [167.3, 174.2]],
    "ref_text": "<exactly what is said in those clips, in order>"
  }
}
```

`from` names a prepped clip. Its separated `vocals.wav` supplies the audio.
A clip can also come from any other prepped part, written `["episode-03", 81.2,
84.9]`, so a bit part can pool clean lines from every part it appears in.
`dub.py` joins the clips (with 0.3 s gaps) into `work/voices/<key>.wav` and
creates a VoiceStudio profile on first use. Changing a voice's entry re-renders
every line that voice speaks, in every part. To give a character a better
voice without re-rendering parts already approved, add it under a new key
(`speaker_1_v2`) and use that from then on.

A character can have a second reference in another register, built by
`register.py build` (see "The reference sets the register" below). Its entry
names the character: `"register_of": "speaker_1", "register": "calm"`. Casting,
pace estimates and QA treat it as the same character; only the delivery
differs.

### script.json

```json
{"lines": [
  {"start": 4.30, "end": 5.90, "voice": "speaker_1", "ja": "こんにちは", "text": "Hello."}
]}
```

- `start`/`end`: the window the English line may occupy.
- `voice`: a key in `voices.json`.
- `ja`: the corrected original.
- `text`: the English that is spoken.
- `instruct` (optional): OmniVoice tags, comma-separated, from a fixed
  vocabulary only. The useful one is `whisper`; the others cover gender, age,
  pitch and accent. `dub.py` rejects anything else, because free text aborts the
  whole render inside VoiceStudio. There is no emotion control.
- `effect` (optional): VoiceStudio's voice processing for this line, overriding
  `--effect`. The options are `broadcast` (default), `raw`, `cinematic`, `warm`,
  `bright`, `podcast`, and from the local patch `echo` for flashbacks and `pa`
  for public-address announcements; see the gotchas below.
- `take` (optional): a number that gives a line repeated word for word its own
  take. VoiceStudio keys takes by the line's voice and text, so two identical
  lines in one part would collide.
- `keep_voice` (optional): `true` stops `register.py route` from moving the line
  to another of the character's references, e.g. when one of them can't say a
  word in it.
- `line_ref` (optional): `false` keeps the character's reference for this line
  instead of its own original (see "Render").

A top-level `"render": {"line_ref": true, "fill": true}` records how the part is
rendered (see "Render"). `dub.py generate` writes it on the first run: both on
for a part never rendered, both off for a part rendered before these became the
default, so its approved takes don't change.

With `concise` timing (the default), each line starts at the window start. With
`fill` on, its pace is then adjusted so it ends where the actor stops (see
"Render"); otherwise it's spoken at its natural pace. It may run up to 0.3 s past
the end (`--overflow`), but not into the next window. Outside all windows the
original soundtrack plays.

A part with no lines at all (a commercial break recorded with the show, the
closing credits) gets a `script.json` with an empty `"lines"` list, and
`assemble.py` uses its original audio.

A top-level `"captions": [{start, end, ja, text}]` list holds speech that stays in
the original audio but still belongs in the subtitles. Examples: dialogue already in
English, names, whispers, and bit parts too short to clone. `assemble.py` adds the
list to both subtitle tracks.

## Workflow

### 1. Prep and draft

```powershell
& $py split.py videos\episode.mkv          # only for long videos: ~3-minute parts
& $py dub.py prep videos\episode-parts\episode-01.mp4
& $py draft.py episode-01                  # after the cast exists: --cast key,key,...
```

`prep` separates the voices and prints Whisper's rough transcript. It also
switches VoiceStudio to pyannote when it's installed.

For cleaner references, try `separate.py episode-01` (its own environment;
see CLAUDE.md). Residual music in the vocals can affect clones, transcripts,
register measurements and level matching. Compare the vocals and background
on your material before choosing a separator. Run separation before drafting
and building voices. `--restore` restores htdemucs stems; changing stems under
approved takes also changes their background and level measurements.

`draft.py` produces the first draft:

- a careful Whisper pass over the separated vocals;
- every phrase snapped to where Silero VAD hears speech;
- focused re-transcription of speech the full pass skipped;
- known Whisper hallucinations flagged ("ご視聴ありがとうございました" on music);
- a suggested speaker for each phrase, with score and runner-up;
- speech already in English, found by Whisper's language detection and
  transcribed in English. Forced to Japanese, Whisper silently *translates*
  English speech, so without this a scene in English reads like Japanese
  dialogue;
- long hallucinations re-transcribed. A stock phrase ("ご視聴ありがとうございました")
  stretched over many seconds can hide real speech. Each speech stretch is
  re-transcribed in Japanese and English; review the result in context.

Short or weak matches get a `?`, and any speech left uncovered is listed.

Whisper still mishears. Re-listen to stretches that look garbled with
`asr_words.py episode-01 <t0> <t1>`. It transcribes that window of the clip's
separated vocals with word times and prints where the VAD hears speech, and a
focused pass often gets what the full pass missed. Correct remaining errors
using surrounding dialogue, on-screen text and consistent terminology.
Keep source excerpts in `work/NOTES.md`.

`draft.py --model` and `asr_words.py --model` accept faster-whisper models.
Compare recognisers on your own corrected samples; confidence alone does not
prove that a transcription is right.

`second.py <clip>` compares ReazonSpeech and SenseVoice with the draft and
flags lines where both disagree with Whisper. Re-listen with `asr_words.py`
and decide from context. It needs its own environment (see CLAUDE.md).

### 2. Cast

Automatic diarisation is only a hint. It merges similar voices and splits
others. Decide each line's speaker from:

1. **Speaker embeddings.** They're the most reliable signal. `draft.py` scores each
   phrase against the cast using WeSpeaker, which ships with pyannote. Each
   character is represented by its voice clips plus up to 30 lines already cast
   to it in the show's other scripts, so casting gets surer with every directed
   part. A clear winner usually scores 0.5–0.9 and beats the runner-up by 0.2 or
   more. Lines under ~0.6 s are unreliable. For a new show or a batch of new
   characters, run `cast.py part-05 part-06 ...` first: it clusters every line
   across parts by voice, so each cluster is one person. The same person in a
   different register (a quiet monologue, a campy aside) can form a separate
   cluster, so check the nearest-cast hint. Name the clusters once, build their
   voices, then re-run `draft.py`.
2. **Video frames** at the line's time: who is in close-up with their mouth open.
   On-screen names and role captions can help identify a speaker.
   ```powershell
   & $py frames.py grid.jpg videos\episode-parts\episode-02.mp4 84:announce 121.5:interjection 147.5
   ```
3. **Pitch** (`pitch.py`, `contour.py`). Rough guide: male 80–180 Hz, female
   200–450 Hz. Actors go outside their range when playful or shouting, so a
   theatrical male lead can hit 400 Hz. Never cast on pitch alone.
4. The dialogue itself: who would say it, and who answers.

For a new character, build a voice from 5–15 s of **clean, single-speaker**
speech (see the reference-leakage gotcha below). 3–4 s works for a bit part.
Pick only clips bounded by real pauses. Run `asr_words.py <clip> t0 t1` around a
candidate: if one VAD speech span runs across the boundary into another
speaker's line, skip that clip. For anyone with less than 3 s of clean speech
(walk-ons, a single whisper), or for a line that's a name, keep the original
audio and put the line in `captions`.

Pick reference lines in the character's usual register. Each line's own
original normally sets its delivery (see "Render"). The character's reference
holds the voice steady, and it alone sets the delivery of lines that can't use
their own original (see "The reference sets the register").

### 3. Translate and time

- **Translate for meaning, in the character's voice**, not word for word. Keep
  running jokes and motifs consistent across a show, and record the choices in
  `work/NOTES.md`.
- **Windows must cover all of the original speech.** Anything outside a window
  plays in the original language. Leave pure hums, laughs and eating noises
  (うーん, んー) uncovered: they're language-neutral and the actor's own.
- **Snap windows to real speech, not Whisper's timestamps.** Whisper's word times
  run about 0.4 s early. Find true onsets with Silero VAD on `vocals.wav`
  (`faster_whisper.vad.get_speech_timestamps`, `min_silence_duration_ms≈120`) or
  with `contour.py`. `onsets.py` gives a quicker energy-based check, but it hits
  its ±0.6 s search limit on noise.
- **Split long lines at the actor's pauses** so the English lands where the lips
  move.
- **End each window in a pause, with nobody else inside it.** A line whose
  window is clean gets the actor's own delivery (see "Render"). One where the
  next speaker starts before the window ends, or the actor is cut mid-sound,
  falls back to the character's reference. In overlapping dialogue, trimming a
  window's end back to the actor's last pause is often enough.
- **Size the text:** each clone follows its actor's pace, from about 8 to 16
  letters per second. Short lines cost more than their length suggests: even
  "Really?" takes 0.8–1.2 s. `fit.py <clip>` computes the length OmniVoice will
  give every line not rendered yet, with OmniVoice's own rule and the reference
  the render will use: typically within 0.2 s, nine lines in ten within 0.4 s,
  about as close as two takes of one line are to each other. It needs no
  earlier renders, so it works on a new show's first part. It first lists the
  lines that will fall back to the character's reference (snap those windows if
  you can), then flags `OVER?` and `risk` against the room each line has, and
  `SHORT?` for English that ends well before the actor stops at natural pace.
  Run it before the first render and fix the flags. For `OVER?`: widen short
  windows into following silence, merge them with a neighbour, or cut words.
  For `SHORT?`: write fuller English, closer to everything the actor says.
  Don't count on fill to slow a short line down (see "Render").
- **Why the pace varies.** OmniVoice fixes a take's length before generating it:
  the reference's duration, times the English text's weight, divided by the
  weight of the reference's transcript. A kanji weighs 3, kana 2.2, a digit
  3.5, a Latin letter 1 and a space 0.2 (`omnivoice/utils/duration.py`).
  Anything under 2 s is stretched towards 2 s, which is why short lines run
  long. With per-line references the reference includes the line's own
  original, so a line the actor says quickly, dense in kanji, comes out fast in
  English, and one said slowly comes out slow. The same English can take very
  different times on two lines of one character; `fit.py` accounts for this.
- **Keep what's already in the target language.** If characters speak English in
  the original, leave it and caption it. `draft.py` tags such lines
  `[English]`; `asr_words.py <clip> t0 t1 --lang en` re-listens to them. If the
  video has burned-in Japanese subtitles there, use them for the captions' `ja`.
- **Voice-over can be freer.** A narrator isn't on screen, so merge clipped
  narration fragments into full sentences. Short sentence fragments can be
  harder to synthesize clearly.

### 4. Render

```powershell
& $py dub.py generate episode-01 --match-level
```

If a line overflows, VoiceStudio stops at the first one. `dub.py` then prints
every line that doesn't fit, using VoiceStudio's exact rule: the whole clip
against the window, plus up to 0.25 s of the following gap, plus the 0.3 s
overflow budget. Fix them all in one pass. `fit.py` prints the same table
anytime. `generate` also warns about lines ending in "…", which tend to swallow
their last word.

Only new, changed or re-rolled lines are synthesized, so fixing an overflow
costs just the lines you touched, and timing or level changes are a re-mix of
about 5 s. (Unpatched VoiceStudio ignores partial re-renders until a job's
first render succeeds, so every failed first attempt re-rolls every take;
`voicestudio-local-fixes.patch` fixes that.)

`--match-level` sets each line's gain from the original actor's level. The
clip-wide offset is matched fully and each line's deviation by half, so
whispers stay quieter than shouts. Without it, dialogue comes out about 10 dB
louder than the film mix.

`--steps` defaults to 32, OmniVoice's quality preset; VoiceStudio's own default
is 16. `--effect` picks the voice processing. The default is `broadcast`;
compare alternatives on your source. `dub.py` records what each line was made
with (`work/<clip>/rendered.json`), so changing steps, effect or instruct
re-synthesizes exactly the affected lines.

By default (the script's `render` block) each take follows the actor's own
delivery of its line and ends where the actor stops:

- **Per-line references** (`line_ref`). Each line's reference is the voice's
  clips from `voices.json`, a pause, then the actor's original of this very
  line, with its `ja` after the voice's transcript. OmniVoice copies its
  reference's delivery and accent, so the take follows the actor on that line
  and keeps their accent, while the clips hold the voice steady. This replaces
  sorting lines into calm and raised: the delivery comes from the line itself.
  A line keeps the character's reference when it has `"line_ref": false`, a
  room effect (`echo`, `pa`; the original's room would be cloned), a window too
  long to fit OmniVoice's 20 s limit alongside the clips, or a window that isn't
  clean. A window isn't clean if speech runs on past its end: the reference would
  end mid-sound, and OmniVoice finishes that sound at the start of the take. Nor
  is it clean if Whisper hears something other than the line's `ja` (crosstalk),
  or if the voice is nearer another character. `generate` prints each line that
  falls back and why. A line whose window overlapped the next speaker came out
  as gibberish on every take until this check excluded it, so don't force
  one. Snap its window instead (see "Translate and time"). A take's reference
  depends on its window, so moving a window re-synthesizes that line.
- **Fill** (`fill`). After the natural-pace render, `generate` finds each actor's
  last sound in the window (Silero VAD on the vocals). It then re-renders every
  take ending more than 0.25 s away from it, at the OmniVoice speed that lands it
  there, within 0.8–1.25. OmniVoice sets the take's length from the speed;
  nothing is time-stretched. Two passes converge, and a line that overflows at
  its natural pace gets sped up instead of stopping the render. Fill only
  aligns where a line ends, not pauses inside it. Lines stuck at 0.8 have
  English much shorter than the actor's speech (`fit.py` flags them `SHORT`):
  give them fuller text, and listen to slowed lines for drawl. A take asked
  for at 0.8 often isn't slower at all: OmniVoice says the words at its own
  pace and leaves the extra time silent, and the silence is trimmed. Only
  fuller text fills those lines. Fill also absorbs most natural-pace
  overflows, which otherwise stop the render and each need a reword or a wider
  window.

`--no-line-ref` and `--no-fill` (or `--line-ref`, `--fill`) override the script
for one run, e.g. for an A/B job. Both change takes, so try them on a copy of an
approved part rather than on the part itself.

`--engine indextts2 --emo-alpha 0.9` (experimental) renders with IndexTTS 2.5
instead of OmniVoice. Each line gets the actor's own line from the separated
vocals as an emotion reference, and the voice still comes from `voices.json`.
The engine is VoiceStudio's global setting: `dub.py` switches it for the render
and switches back afterwards. It needs the local patch, which adds the
per-line reference, and it's slow, about 8 s a line against OmniVoice's 1 s.
The reference carries the actor's pacing as well as their delivery: without
it, IndexTTS runs well past the actor and overflows many windows. On a scene
with a wide emotional range, its takes followed the actors' delivery more
closely than any OmniVoice approach. On a quiet scene there was little to
follow and no gain. The costs are likeness, which is lower than OmniVoice's,
and the accent. OmniVoice keeps the actor's accent from the reference, while
IndexTTS speaks its own native English. Whisper's language confidence on the
takes measures this cheaply: clones of non-native actors score well below
native speech. A listener may find the actor's own accent more authentic, so
render it as a separate job and compare by ear.

How these were chosen, and what else was tried, is in
[research.md](research.md).

### 5. QA

- `qa.py episode-01` transcribes the finished MP4 in English and lines it up with
  the script. Word timestamps drift by a line or so in quiet passages, so a
  word appearing in the neighbouring window is usually fine. Whisper also
  invents words on background noise; check `energy.py` before
  worrying.
- **Check each rendered line on its own too:** `lineqa.py episode-01`. The
  full-mix QA hid these problems:
  - junk at the start of lines (reference leakage, below)
  - dropped words in fast lists
  - meaning flips like "can" heard as "can't" (any change in negation is flagged)
  - words a voice can't say
  - lines that came out silent

  Numbers, units, contractions and "Dr." are normalized, so formatting doesn't
  count as a failure. Triage each flag. A random glitch (a dropped word, a
  mangled first word) usually clears with a fresh take: `lineqa.py episode-01
  --reroll` queues the flagged lines, and the next `generate` re-rolls them.
  A word the voice can't say needs rewording. Every take is a new roll of the
  dice, so re-check after each render. Judge each flag against the intended
  text and the audio. Names and specialist vocabulary can confuse QA even when the take is
  correct; flag uncertain cases for the listener.
- Listen. Machines can't judge acting.

### 6. Package

```powershell
& $py assemble.py episode --parts 1-4      # or: assemble.py <clip> for an unsplit clip
```

This produces one MP4. The video is stream-copied from the source, so part cuts
leave no seams. Audio 1 is the dub (default) and audio 2 is the original
soundtrack. The subtitles are English (the dub's text) and Japanese (the
corrected original), both including `captions`. Any text subtitle tracks already
in the source are kept, and `--sub file.srt:lang[:title]` adds more (for example
official subtitles). It also writes sidecar `.en.srt` / `.ja.srt` files.

## Gotchas

- **Voice-reference leakage (the big one).** If a clip in `voices.json` cuts a
  word off, or includes someone else's voice, OmniVoice "finishes" it at the
  start of *every* line in that voice. Incomplete references can leak extra
  syllables into each take. Clips taken
  straight from Whisper timestamps are cut off because those run early. Snap
  clips to real speech boundaries, keep neighbours out, then check the joined
  reference with `asr_words.py work\voices\<key>.wav`. Every phrase must be
  complete, and `ref_text` must match what is actually said.
- **A click just before the English starts = the actor chopped mid-sound.** At
  each window start VoiceStudio crossfades from the original mix, voice
  included, to the voice-free bed in only 10 ms. Speech detection misses soft
  attacks (a breath, a consonant) by 20–100 ms. If the actor is already
  sounding when the window opens, that sliver of Japanese plays and is then
  cut. It's rare enough to sound random ("sometimes the start is glitchy").
  `edges.py <clip> --apply` finds these and starts the window 20 ms before the
  true attack. It only moves clear cases: a voice within 16 dB of the line at
  the cut that began 20–130 ms before the window. Shorter fragments weren't
  heard, and longer ones are usually someone else's sound. It's timing only, so
  the re-render is a re-mix and approved takes stay. `draft.py` applies the same
  rule to new drafts. Windows you set by hand bypass it, so run `edges.py`
  after writing a script. A `skip` with a long shift (150–300 ms) and a voice
  near the line's level at the cut is often the same speaker starting early.
  Confirm it with `asr_words.py` (Whisper's start plus ~0.35 s) and move it by
  hand. Leave it when the sound could be someone else, such as a sigh louder
  than the line itself. The fix is best checked by ear on the flagged lines.
  Window ends are the mirror image: a voice still sounding when the window
  closes plays a clipped tail of the original after the English. `edges.py`
  moves the end past short tails (same rule) and notes the rest for manual review.
- **Match reverberation to the source.** `tails.py <clip>` compares the
  original voice tail with the rendered take. Music or a following sound can
  look like reverb, so review the scene before choosing an effect. `echo`
  provides heavy reverb with a 1 s tail; `pa` provides public-address tone
  and reverb with a 0.5 s tail. These are starting presets: compare them on
  your material. Extend the speech window over the original tail without
  covering the next speaker. Effects lengthen the take, which `fit.py` does
  not predict; leave room and check the rendered duration.
- **Timing strategy.** Use `concise`, dub.py's default: natural pace, placed at the
  window start, and it fails loudly on overflow. `strict_slot` (the VoiceStudio UI
  default) stretches every line to fill its window, which gives slowed or rushed
  speech.
- **Words a cloned voice cannot say.** A fresh take can fix a random
  glitch; persistent pronunciation problems need a clearer phrase, an
  expanded abbreviation, or a different reference. Keep production-specific
  examples in `work/NOTES.md`. A trailing ellipsis can swallow the last word.

- **Repeated phrases collapse.** Give each repetition its own line at
  the speaker's pauses, with `"take": n` so each gets a distinct take.
- **Short replies need care.** Allow room after short lines where possible.
  If a very short acknowledgement has no room and repeated takes fail,
  retain the original audio and add a caption.
- **No acting direction with OmniVoice.** VoiceStudio's `direction` field and
  free-text `instruct` values (such as "excited") crash the dub. Its only style
  tag is `whisper`. Delivery comes from the reference, which is why each line's
  reference ends with the actor's own original of it (per-line references).
- **The reference sets the register.** A clone copies delivery as well as
  voice identity. With per-line references this matters only for lines that
  fall back to the character's reference. When many of a character's lines
  fall back and they sound wrong, e.g. a character whose reference is projected
  but whose fallback lines are quiet asides, give them a reference in their
  register with `register.py`:
  1. `report` measures pitch, vocal effort and loudness relative to each
     speaker's own lines and flags mismatches.
  2. `build <voice> calm|raised` builds another reference from clean lines
     with matching voice and transcript. It requires at least 5 s of speech
     and a register difference of at least 0.8.
  3. `route <clip> --apply` selects a reference only when it is nearer by
     at least 0.5. Only moved lines need new takes.
  4. Render and QA. `fit.py` learns each reference's pace. Use
     `"keep_voice": true` for lines that should keep a particular reference.
  5. `check` verifies that the takes deliver the intended register;
     `drop <voice>` retires a reference that fails this check.

  Loudness in the mix is not the same as vocal effort. Review both the
  source and the takes; these tools do not provide emotion control.
- **VoiceStudio reuses separated stems by video content.** `prep` copies the
  stems of the newest job made from the same video, including stems that
  `separate.py` swapped in. So after re-separating one job, a new job for the
  same video (an A/B copy, say) starts with Roformer stems, not htdemucs, even
  though VoiceStudio only runs htdemucs. Check `separator.json` in the new job
  folder, and use `separate.py --restore` or copy the stems you want.
- **After an NVIDIA driver update, restart the backend.** A backend that was
  running during the update keeps "CUDA out of memory" errors on an empty GPU
  and silently runs Whisper on the CPU.
- **Speaker detection.** Automatic diarisation can merge similar voices or
  split one speaker. Confirm casting with multiple signals. Sortformer can't
  handle clips over 120 s or more than 4 speakers, ignores
  count hints, and has CC-BY-NC weights.
- **VoiceStudio bugs (fixed by `voicestudio-local-fixes.patch`).** Dubbing
  transcription ignored the source-language hint, so short Japanese clips were
  heard as English (`backend/api/routers/dub_core.py`). OmniVoice loaded in
  float16 on CPU, where it is about 7× slower (`backend/services/model_manager.py`).
  A render that stopped at a timing overflow didn't record its takes as
  reusable, so the next one re-synthesized everything
  (`backend/api/routers/dub_generate.py`). Reverb presets stopped dead where
  the dry take ended, cutting their own tail (`backend/services/audio_dsp.py`,
  which also gains the `echo` preset). All are worth reporting upstream.
