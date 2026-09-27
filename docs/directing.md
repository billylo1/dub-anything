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
`dub.py` joins the clips (with 0.3 s gaps) into `work/voices/<key>.wav` and
creates a VoiceStudio profile on first use. Changing a voice's entry re-renders
every line that voice speaks.

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

With `concise` timing (the default), each line is spoken at its natural pace from
the window start. It may run up to 0.3 s past the end (`--overflow`), but not
into the next window. Outside all windows the original soundtrack plays.

## Workflow

### 1. Prep

```powershell
& $py split.py videos\episode.mkv          # only for long videos: ~3-minute parts
& $py dub.py prep videos\episode-parts\episode-01.mp4
```

`prep` prints Whisper's transcript with rough speaker labels. Treat it as a
first draft. For a careful transcript with word timings, run `asr_words.py` on
the separated vocals. They are in `$env:OMNIVOICE_DATA_DIR\dub_jobs\<job_id>\vocals.wav`, where `job_id` is in
`segments.json`. Re-run it on short cuts (`ffmpeg -ss A -t D -i vocals.wav cut.wav`,
pass `A` as the offset) wherever lines are missing or garbled. A focused pass
often recovers lines the full pass skipped.

Correct mishearings using surrounding dialogue and on-screen text.

### 2. Cast

Automatic diarisation is only a hint. It merges similar voices and splits
others. Decide each line's speaker from:

1. **Speaker embeddings.** They're the most reliable signal. Pick 3–4 phrases you
   are sure of per character, then score every phrase against each character with
   the WeSpeaker model that ships with pyannote (`pyannote/wespeaker-voxceleb-resnet34-LM`).
   A clear winner is usually 0.3–0.8 cosine above the rest; clips under ~0.5 s
   are unreliable. Load the checkpoint from its local file after
   `torch.serialization.add_safe_globals([TorchVersion, Problem, Resolution, Specifications])`.
   pyannote 3.4's `from_pretrained` breaks on huggingface_hub 1.x.
2. **Video frames** at the line's time: who is in close-up with their mouth open.
   ```powershell
   ffmpeg -ss 36 -i clip.mp4 -t 3 -vf "fps=2,scale=320:-1,tile=6x1" -frames:v 1 strip.jpg
   ```
3. **Pitch** (`pitch.py`, `contour.py`). Rough guide: male 80–180 Hz, female
   200–450 Hz. Actors go outside their range when playful or shouting, so a
   theatrical male lead can hit 400 Hz. Never cast on pitch alone.
4. The dialogue itself: who would say it, and who answers.

For a new character, build a voice from 5–15 s of **clean, single-speaker**
speech (see the reference-leakage gotcha below).

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
- **Size the text:** OmniVoice speaks English at about 14–17 characters per second.
  Short lines cost more than their length suggests: even "Really?" takes 0.8–1.2 s.
  Widen short windows into following silence, merge them with a neighbour, or
  cut words.

### 4. Render

```powershell
& $py dub.py generate episode-01 --match-level
```

If a line overflows its window, the render stops with `dub_timing_overflow` and
names the segment. Run `fit.py episode-01` to see every line's length against
its room, then shorten the text or widen the window and render again. After one
successful render only changed lines are re-synthesized. Timing and level
changes are just a re-mix of about 5 s.

`--match-level` sets each line's gain from the original actor's level. The
clip-wide offset is matched fully and each line's deviation by half, so
whispers stay quieter than shouts. Without it, dialogue comes out about 10 dB
louder than the film mix.

### 5. QA

- `qa.py episode-01` transcribes the finished MP4 in English and lines it up with
  the script. Word timestamps drift by a line or so in quiet passages, so a
  word appearing in the neighbouring window is usually fine. Whisper also
  invents words on background noise; check `energy.py` before
  worrying.
- **Check each rendered line on its own as well.** The full-mix QA hid these
  problems, which isolated transcription of each `seg_en_<id>.wav` in the job
  folder exposes:
  - junk at the start of lines (reference leakage, below)
  - dropped words in fast lists
  - ambiguities like "can" heard as "can't"
- Listen. Machines can't judge acting.

## Gotchas

- **Voice-reference leakage (the big one).** If a clip in `voices.json` cuts a
  word off, or includes someone else's voice, OmniVoice "finishes" it at the
  start of *every* line in that voice. Incomplete references can leak extra syllables into each take. Clips taken
  straight from Whisper timestamps are cut off because those run early. Snap
  clips to real speech boundaries, keep neighbours out, then check the joined
  reference with `asr_words.py work\voices\<key>.wav`. Every phrase must be
  complete, and `ref_text` must match what is actually said.
- **Timing strategy.** Use `concise`, dub.py's default: natural pace, placed at the
  window start, and it fails loudly on overflow. `strict_slot` (the VoiceStudio UI
  default) stretches every line to fill its window, which gives slowed or rushed
  speech.
- **Partial re-render** only works after one successful render of the job. If
  the first render fails at assembly, the next one re-synthesizes everything.
- **Pronunciation.** Rephrase persistent errors using clearer words.
  Review names and specialist terms with the listener.
- **Speaker detection.** Automatic diarisation can merge similar voices or split one speaker.
  Confirm casting with multiple signals. Sortformer can't handle clips over 120 s or more than 4 speakers, ignores
  count hints, and has CC-BY-NC weights.
- **VoiceStudio bugs (fixed by `voicestudio-local-fixes.patch`).** Dubbing
  transcription ignored the source-language hint, so short Japanese clips were
  heard as English (`backend/api/routers/dub_core.py`). OmniVoice loaded in
  float16 on CPU, where it is about 7× slower (`backend/services/model_manager.py`).
  Both are worth reporting upstream.
