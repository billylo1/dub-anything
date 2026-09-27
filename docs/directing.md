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
  `bright` and `podcast`.

With `concise` timing (the default), each line is spoken at its natural pace from
the window start. It may run up to 0.3 s past the end (`--overflow`), but not
into the next window. Outside all windows the original soundtrack plays.

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
switches VoiceStudio to pyannote when it's installed. `draft.py` is the real
first draft:

- a careful Whisper pass over the separated vocals;
- every phrase snapped to where Silero VAD hears speech;
- focused re-transcription of speech the full pass skipped;
- known Whisper hallucinations flagged ("ご視聴ありがとうございました" on music);
- a suggested speaker for each phrase, with score and runner-up.

Short or weak matches get a `?`, and any speech left uncovered is listed.

Whisper still mishears. Re-listen to stretches that look garbled with
`asr_words.py episode-01 <t0> <t1>`. It transcribes that window of the clip's
separated vocals with word times and prints where the VAD hears speech, and a
focused pass often gets what the full pass missed. Then correct the remaining errors using surrounding dialogue, on-screen
text and consistent terminology. Keep source excerpts in `work/NOTES.md`.

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
- **Size the text:** each clone follows its actor's pace, from about 8 to 16
  letters per second. Short lines cost more than their length suggests: even
  "Really?" takes 0.8–1.2 s. `fit.py <clip>` estimates every line not rendered
  yet from its voice's rate on the lines already rendered (typically within
  0.2 s) and flags `OVER?` and `risk` against the room each line has. Run it
  before the first render and fix the flags: widen short windows into
  following silence, merge them with a neighbour, or cut words.
- **Keep what's already in the target language.** If characters speak English in
  the original, leave it and caption it.
- **Voice-over can be freer.** A narrator isn't on screen, so merge clipped
  narration fragments into full sentences. Short sentence fragments can be harder to synthesize clearly.

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
is 16. `--effect` picks the voice processing. The default is `broadcast`; compare alternatives on your source. `dub.py` records what each cached line was made
with (`work/<clip>/rendered.json`), so changing steps, effect or instruct
re-synthesizes exactly the affected lines.

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
  dice, so re-check after each render. Judge each flag against the intended text and the audio. Names and
  specialist vocabulary can confuse the QA recogniser even when the take is
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
  start of *every* line in that voice. Incomplete references can leak extra syllables into each take. Clips taken
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
  than the line itself. The fix is best checked by ear on the flagged lines;
  `edges.py` also notes window ends where the original voice is still
  sounding.
- **Timing strategy.** Use `concise`, dub.py's default: natural pace, placed at the
  window start, and it fails loudly on overflow. `strict_slot` (the VoiceStudio UI
  default) stretches every line to fill its window, which gives slowed or rushed
  speech.
- **Pronunciation.** Rephrase persistent errors with clearer words.
  Review names and specialist terms with the listener. Keep examples from
  source material in `work/NOTES.md`.

- **Short replies need care.** Widen windows into following silence
  when possible. If a short acknowledgement cannot fit or render clearly,
  retain the original audio and caption it.
- **No acting direction with OmniVoice.** VoiceStudio's `direction` field and
  free-text `instruct` values (such as "excited") crash the dub. Its only style
  tag is `whisper`. Delivery comes from the reference clip, so pick references
  in the character's usual register.
- **After an NVIDIA driver update, restart the backend.** A backend that was
  running during the update keeps "CUDA out of memory" errors on an empty GPU
  and silently runs Whisper on the CPU.
- **Speaker detection.** Automatic diarisation can merge similar voices or split one speaker.
  Confirm casting with multiple signals. Sortformer can't handle clips over 120 s or more than 4 speakers, ignores
  count hints, and has CC-BY-NC weights.
- **VoiceStudio bugs (fixed by `voicestudio-local-fixes.patch`).** Dubbing
  transcription ignored the source-language hint, so short Japanese clips were
  heard as English (`backend/api/routers/dub_core.py`). OmniVoice loaded in
  float16 on CPU, where it is about 7× slower (`backend/services/model_manager.py`).
  A render that stopped at a timing overflow didn't record its takes as
  reusable, so the next one re-synthesized everything
  (`backend/api/routers/dub_generate.py`). All three are worth reporting
  upstream.
