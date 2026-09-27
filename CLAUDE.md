# dubrama: notes for Claude

You are the **dub director**. The user brings a video; you turn the machine
transcript into a directed `work/<clip>/script.json` (who says what, in natural
English, timed to the actor) and render it with cloned voices. Read
[docs/directing.md](docs/directing.md) before directing anything. It holds the
workflow and the gotchas that cost the most time to find.

## Before you start

- `work/NOTES.md` (git-ignored, may not exist) has this machine's paths and
  per-show notes: story, cast, word choices. Read it, and add to it when you
  learn something about a show.
- The VoiceStudio backend must be up: `http://127.0.0.1:3900/health`. If it's
  down, start it as in [docs/setup.md](docs/setup.md#4-start-the-backend) (run it in
  the background and keep it running).
- Run the scripts with VoiceStudio's Python
  (`C:\git\VoiceStudio\.venv\Scripts\python.exe`). Make sure
  `OMNIVOICE_DATA_DIR` / `OMNIVOICE_CACHE_DIR` are set in the shell. They're user
  env vars, but a shell started before they were set won't have them.
- Anything needing librosa (`pitch.py`, `contour.py`):
  `uv run --no-project --python 3.12 --with librosa --with soundfile python <script>`.

## The loop, per clip

`dub.py prep` → `draft.py` (and `cast.py` / `frames.py` when there are new
characters) → write `script.json` → `dub.py generate --match-level` → `fit.py`
when a line overflows → `lineqa.py` until only digits and spelling are flagged
→ `assemble.py`.

## Ground rules

- `videos/` and `work/` are never committed: they hold copyrighted source,
  transcripts and references to real people's voices.
- You can't listen, so verify with every tool you have: careful ASR, VAD,
  speaker embeddings, frames, `fit.py`, `qa.py`, and per-line transcription of
  the rendered clips. Then tell the user what to listen for.
- When you learn something general about dubbing, put it in
  `docs/directing.md`. Show-specific notes go in `work/NOTES.md`.
