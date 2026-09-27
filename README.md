# dubrama

Dub video into another language **in each actor's own voice**, keeping the
original music and sound effects. Everything runs locally on your GPU.

dubrama is a small toolkit around the dubbing API of
[VoiceStudio](https://github.com/debpalash/VoiceStudio) (a local,
ElevenLabs-style app). The machine does the heavy lifting: separating voices from
the soundtrack, transcribing, cloning voices and mixing. A **director** does the
part machines still get wrong: fixing the transcript, deciding who says what,
translating for meaning and timing each line to the actor's mouth. The director
can be you, or an AI coding agent such as [Claude Code](https://claude.com/claude-code).
This repo's [CLAUDE.md](CLAUDE.md) teaches it the job.

Built and tested for **Japanese → English** on Windows with an NVIDIA GPU.

## How it works

| Step | Model | Where |
|---|---|---|
| Separate voices from music/SFX | Demucs htdemucs | VoiceStudio (`dub.py prep`) |
| Transcribe the original language | Whisper large-v3 (faster-whisper) | VoiceStudio |
| Rough "who speaks when" | pyannote 3.1 or Sortformer | VoiceStudio |
| Fix transcript, cast, translate, time every line | **the director** → `script.json` | you / Claude |
| Clone each actor's voice speaking the new language | OmniVoice (k2-fsa, zero-shot) | VoiceStudio (`dub.py generate`) |
| Mix over the preserved background, export MP4 + SRT | ffmpeg | VoiceStudio |

Nothing is uploaded anywhere. Hugging Face is only used to download models.

## What directing looks like

Each clip gets a `work/<clip>/script.json`. One entry per line of dialogue:

```json
{"lines": [
  {"start": 4.30, "end": 5.90, "voice": "speaker_1", "ja": "こんにちは", "text": "Hello."},
  {"start": 6.40, "end": 8.80, "voice": "speaker_2",  "ja": "お元気ですか", "text": "How are you?"}
]}
```

`start`/`end` is the window the English line may occupy, snapped to where the
actor really speaks. `voice` is a character from `work/voices.json`: a few
seconds of that actor's clean speech, cut from the separated vocals, which
OmniVoice clones. Outside the windows you hear the original soundtrack, so
laughs, sighs and hums stay the actor's own.

## Quick start

Setup (VoiceStudio, models, GPU) is in **[docs/setup.md](docs/setup.md)**. Once
the backend is running on `http://127.0.0.1:3900`:

```powershell
$py = "C:\git\VoiceStudio\.venv\Scripts\python.exe"   # dubrama runs in VoiceStudio's venv

& $py split.py videos\episode.mkv                   # long video -> ~3-minute parts at pauses
& $py dub.py prep videos\episode-parts\episode-01.mp4   # separate, transcribe, diarize
# ...direct: write work\episode-01\script.json (and voices in work\voices.json)
& $py dub.py generate episode-01 --match-level      # clone, synthesize, mix, export
& $py qa.py episode-01                              # transcribe the dub back and compare
```

The finished MP4 and SRT land in a `dubbed` folder next to the source clip. The
full directing workflow, file formats and hard-won gotchas are in
**[docs/directing.md](docs/directing.md)**.

## Tools

| Script | Does |
|---|---|
| `dub.py prep <video>` | Upload, separate, transcribe, diarize → `work/<name>/segments.json`, `ja.srt` |
| `dub.py generate <name> --match-level` | Render `script.json` with cloned voices, levelled against the original actors → MP4 + SRT |
| `split.py <video>` | Cut a long video into ~N-minute parts at pauses in speech (frame-accurate) |
| `asr_words.py <wav> [offset]` | Careful Whisper pass with word timings |
| `onsets.py <name>` | Script windows vs where each actor's voice really starts |
| `fit.py <name>` | Each rendered line's length vs its window |
| `qa.py <name>` | Transcribe the finished dub back and line it up with the script |
| `energy.py <name>` | Per-second loudness of vocals vs background |
| `pitch.py <name>`, `contour.py <name> <t0> <t1>` | Pitch per phrase / in fine steps, for casting (need librosa) |

## Performance

Runtime depends on clip length, hardware, model settings and the number of
lines that need synthesis. Timing and level edits can reuse approved takes.

What's next: [docs/roadmap.md](docs/roadmap.md).

## Responsible use

Only dub media you have the rights to, and only clone voices with the consent a
use requires. Output made from someone else's film or voice is for private use
unless you have permission. `videos/` and `work/` are git-ignored so source
media, transcripts and cloned-voice references stay out of normal commits; do not force-add them.

## License

dubrama is MIT-licensed ([LICENSE](LICENSE)). It talks to VoiceStudio over HTTP
and does not include its code. VoiceStudio is AGPL-3.0, and so is
`voicestudio-local-fixes.patch`, a change to VoiceStudio's source. Models have
their own licenses: pyannote is gated on Hugging Face, and the Sortformer
weights are CC-BY-NC. Check each model before commercial use.
