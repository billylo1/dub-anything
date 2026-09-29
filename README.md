# dubrama

Dub video into another language **in each actor's own voice**, keeping the
original music and sound effects. Everything runs locally on your GPU. Built and
tested for **Japanese → English** on Windows with an NVIDIA GPU.

## How you use it

You don't run the scripts yourself. Open this folder in an AI coding agent
(Claude Code, Codex, Cursor or another harness that reads
[AGENTS.md](AGENTS.md)) and ask it to dub a video. The agent directs the dub,
and `AGENTS.md` tells it how:

- **The scripts** do the heavy lifting: separate voices from music, transcribe,
  clone, mix, and check every line.
- **The agent** makes the calls: who says what, what Whisper misheard, natural
  English timed to the actor's lips, what stays in the original, which QA flags
  are real. It tells you which lines are guesses.
- **You** listen. You judge the acting, the voices and the mix, and decide what
  may be cloned or shared.

In practice:

1. Set up once: [docs/setup.md](docs/setup.md).
2. Put the video in `videos\` and ask, for example: *"Dub the first 10 minutes
   of videos\my-show.mkv."*
3. Answer one question: which voice (below).
4. Listen to what the agent sends and to the moments it asks you to check, and
   say what to change.
5. Watch the result: `videos\<show>-parts\dubbed\<show>.dubbed.p1-4.mp4`, with
   the dub as the default audio track, the original as a second track, and
   English and Japanese subtitles.

## The one choice: the voice

| | **Actor's voice** (default) | **American** |
|---|---|---|
| Sounds like | The actor speaking English, with their own accent | Native American English, converted towards each actor's voice |
| Best for | Live action | Anime and animation |
| Trade-off | Accented English | Looser timing and a little less of the acting; about 4× slower to render |

Not sure? The agent can render one short part both ways first. Everything else
(separation, timing, voice processing) has defaults chosen by ear.

## How it works

![How a dub gets made](docs/process.png)

1. **Split** long videos into ~3-minute parts at pauses in speech.
2. **Separate** the voices from music and effects (Mel-Band Roformer).
3. **Transcribe** the Japanese (Whisper large-v3, with two other recognisers as a
   second opinion). If the picture has burned-in closed captions, they are read
   too: the exact words, often with the speaker's name.
4. **Direct** (the agent): cast each line, correct the transcript, translate,
   and time every line to the actor → `work/<part>/script.json`.
5. **Voice** each line. With the actor's voice, OmniVoice clones the actor,
   prompted with their own delivery of that line. With American, IndexTTS
   speaks native English following the actor's delivery, and Chatterbox then
   converts it to the actor's voice.
6. **Fit, mix and check.** Each take is re-timed to end where the actor stops
   and mixed over the original background; every line is transcribed back to
   catch garbled words.

Outside the dubbed lines you hear the original soundtrack, so songs, laughs and
names stay the actor's own. The diagram is [docs/process.jg](docs/process.jg), a
[Jumpgate](https://github.com/swax/jumpgate) file. It is rendered with
`npm run render -- <path>/docs/process.jg` from Jumpgate's `packages/jumpgate`.

## Doing it by hand

The agent's loop can be run by hand with VoiceStudio's Python
(`$py = "C:\git\VoiceStudio\.venv\Scripts\python.exe"`) and the backend running:

```powershell
& $py split.py videos\episode.mkv                     # long video -> parts at pauses
& $py dub.py prep videos\episode-parts\episode-01.mp4  # upload, separate, transcribe
& $py draft.py episode-01                            # careful transcript, suggested cast
# write work\episode-01\script.json and the cast in work\voices.json
& $py dub.py generate episode-01 --match-level       # clone, time, mix, export
& $py lineqa.py episode-01                           # check each rendered line
& $py assemble.py episode --parts 1-4                # one video with both audio tracks
```

Some steps need their own environments (`separate.py`, `subs.py`, `second.py`;
see [AGENTS.md](AGENTS.md)). The workflow, the script format, every tool and the
gotchas are in [docs/directing.md](docs/directing.md). What was measured and
tried is in [docs/research.md](docs/research.md), and what's next is in
[docs/roadmap.md](docs/roadmap.md).

## Responsible use

Only dub media you have the rights to, and only clone voices with the consent
the use requires. Output made from someone else's film or voice is for private
use unless you have permission. `videos/` and `work/` are git-ignored, so source
media, transcripts and voice references stay out of commits: don't force-add
them, and use invented examples in docs and commit messages.

## License

dubrama is MIT-licensed ([LICENSE](LICENSE)). It talks to
[VoiceStudio](https://github.com/debpalash/VoiceStudio) over HTTP and does not
include its code. VoiceStudio is AGPL-3.0, and so is
`voicestudio-local-fixes.patch`, a change to VoiceStudio's source. Models have
their own licenses:
- pyannote is gated on Hugging Face;
- the Sortformer weights are CC-BY-NC;
- Chatterbox (the American voice's converter) is MIT, and marks its output with
  an inaudible watermark.

Check each model before commercial use. Nothing is uploaded anywhere; Hugging
Face is only used to download models.
