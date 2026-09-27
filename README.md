# dubrama

Dub video into another language **in each actor's own voice**, keeping the
original music and sound effects. Everything runs locally on your GPU.

**dubrama is built to be run by an AI agent.** It is a set of scripts around the
dubbing API of [VoiceStudio](https://github.com/debpalash/VoiceStudio), a local,
ElevenLabs-style app. The scripts are instruments: they separate, transcribe,
measure, render and check, but none of them decides anything. The agent is the
**director**. It reads the evidence the tools produce, makes the calls, and
loops until the checks pass. A person does the one thing the agent can't:
listen.

| | Does | For example |
|---|---|---|
| **The scripts** | Produce evidence and do the heavy lifting | Separate voices from music, transcribe, score each line against each character's voice, grab frames, clone and mix, transcribe every rendered line back |
| **The agent (director)** | Makes the judgement calls, in a loop | Weighs conflicting evidence on who's speaking. Fixes mishearings from context. Translates for meaning and lip timing, and keeps running jokes consistent. Decides what stays in the original (names, whispers, lines already in English). Triages QA flags. Flags its own guesses. |
| **You** | Listen, and decide taste and rights | Judge the acting, the voices and the mix. Choose between A/B versions. Decide what may be cloned or shared. |

[CLAUDE.md](CLAUDE.md) describes the directing workflow for an AI agent.
A person can run the same loop by hand.

Built and tested for **Japanese → English** on Windows with an NVIDIA GPU.

## How it works

![How a dub gets made](docs/process.png)

The diagram is [docs/process.jg](docs/process.jg), a [Jumpgate](https://github.com/swax/jumpgate)
file: open it in Jumpgate to pan, zoom or edit, and re-render with
`npm run render -- <path>/docs/process.jg` from Jumpgate's `packages/jumpgate`.

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
& $py draft.py episode-01                           # careful transcript, real timings, suggested cast
# ...direct: write work\episode-01\script.json (and voices in work\voices.json)
& $py dub.py generate episode-01 --match-level      # clone, synthesize, mix, export
& $py lineqa.py episode-01                          # check every rendered line on its own
& $py assemble.py episode --parts 1-4               # one video: dub + original audio, EN/JA subtitles
```

Each part's dub lands in a `dubbed` folder next to the source clip. `assemble.py`
joins parts into one MP4 with the dub as the default audio track, the original
soundtrack as a second track, and soft subtitles in both languages. The full
directing workflow, file formats and hard-won gotchas are in
**[docs/directing.md](docs/directing.md)**.

## Tools

| Script | Does |
|---|---|
| `dub.py prep <video>` | Upload, separate, transcribe, diarize → `work/<name>/segments.json`, `ja.srt` |
| `draft.py <name>` | Careful transcript snapped to real speech, missed lines recovered, speaker suggested per line → `draft.json` |
| `cast.py <name> ...` | Group lines across parts by voice, so each character gets one voice |
| `frames.py <out.jpg> <video> <t>[:label] ...` | Labelled frame grid: who is on screen at each line |
| `dub.py generate <name> --match-level` | Render `script.json` with cloned voices, levelled against the original actors → MP4 + SRT |
| `fit.py <name>` | Each line's length vs its window: measured once rendered, estimated from the voice's pace before, so overflows are fixed ahead of the first render |
| `edges.py <name> [--apply]` | Find windows that open after the actor has started (a click as the original gets chopped) and move them to the true attack |
| `lineqa.py <name> [--reroll]` | Transcribe each rendered line alone; flag leaked, dropped or garbled words and flipped meanings; queue fresh takes |
| `assemble.py <stem> [--parts 1-4]` | Join parts: dub + original audio tracks, soft EN/JA subtitles, sidecar SRTs |
| `split.py <video>` | Cut a long video into ~N-minute parts at pauses in speech (frame-accurate) |
| `asr_words.py <clip> <t0> <t1>` | Careful re-listen of one stretch of a clip's vocals, with word timings and speech spans (also takes a wav) |
| `qa.py <name>` | Transcribe the finished dub back and line it up with the script |
| `onsets.py`, `energy.py`, `pitch.py`, `contour.py` | Onset, loudness and pitch checks (the last two need librosa) |

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
