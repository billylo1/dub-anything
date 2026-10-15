# Setup

Dub Anything needs a running [VoiceStudio](https://github.com/debpalash/VoiceStudio)
backend, its models, and ffmpeg.

## 1. Tools

### macOS (Apple Silicon or Intel)
- macOS 13.3+ (Ventura or newer). On Apple Silicon (M1/M2/M3/M4), Metal (MPS) acceleration is used automatically.
- Install tools via Homebrew:
  ```bash
  brew install git uv ffmpeg
  ```
  *(Note: Standard Homebrew `ffmpeg` is sufficient. Dub Anything includes automatic PIL fallbacks for casting previews when FFmpeg is built without `drawtext`.)*
- In the clone, turn on pre-commit hooks and git blame filters:
  ```bash
  git config core.hooksPath .githooks
  git config blame.ignoreRevsFile .git-blame-ignore-revs
  ```

### Windows (NVIDIA)
- An NVIDIA GPU with ≥ 6 GB of VRAM. `nvidia-smi` should work.
- NVIDIA driver ≥ 610 if you want `scripts/split.py --encoder h264_nvenc` (current
  FFmpeg builds need NVENC API 13.1). Otherwise use the default x264.
- `winget install Git.Git astral-sh.uv Gyan.FFmpeg`, then open a new terminal.
- In the clone, `git config core.hooksPath .githooks` turns on the pre-commit
  check (ruff format and lint) and `git config blame.ignoreRevsFile
  .git-blame-ignore-revs` keeps formatting commits out of `git blame`.

## 2. Models and Data Directories

### macOS
By default, Dub Anything and VoiceStudio place data in:
- Data / dub jobs: `~/Library/Application Support/OmniVoice/dub_jobs`
- Model caches: `~/Library/Caches/OmniVoice/hf_cache`

To customize these or keep them on a specific drive, add to your `~/.zshrc`:
```bash
export OMNIVOICE_DATA_DIR="$HOME/VoiceStudioData/data"
export OMNIVOICE_CACHE_DIR="$HOME/VoiceStudioData/models"
```

### Windows
Put VoiceStudio's data, its model cache and uv's Python installs somewhere
neutral. The VoiceStudio GUI can share them, and they survive sandboxed shells.
Some sandboxed agent apps (the Claude desktop app, for one) redirect AppData
writes, which breaks `uv sync` ("Missing expected target directory for Python
minor version link").
In PowerShell, then open a new terminal:

```powershell
$d = "C:\VoiceStudioData"
[Environment]::SetEnvironmentVariable("OMNIVOICE_DATA_DIR", "$d\data", "User")
[Environment]::SetEnvironmentVariable("OMNIVOICE_CACHE_DIR", "$d\models", "User")
[Environment]::SetEnvironmentVariable("UV_PYTHON_INSTALL_DIR", "$d\uv\python", "User")
[Environment]::SetEnvironmentVariable("UV_CACHE_DIR", "$d\uv\cache", "User")
[Environment]::SetEnvironmentVariable("HF_TOKEN_PATH", "$env:USERPROFILE\.cache\huggingface\token", "User")
```

Dub Anything's scripts find VoiceStudio's jobs through `OMNIVOICE_DATA_DIR` (or system defaults), so set it in any shell that runs them if you use a custom path.

## 3. VoiceStudio, pinned, with local fixes

### macOS
```bash
git clone https://github.com/debpalash/VoiceStudio.git ~/python/VoiceStudio
git -C ~/python/VoiceStudio checkout -b local-fixes c52618af
git -C ~/python/VoiceStudio apply <path-to-dub-anything>/patches/voicestudio-local-fixes.patch
cd ~/python/VoiceStudio && uv sync
uv run python backend/main.py --diagnose
```
*(On macOS, PyTorch on Apple Silicon uses Metal / MPS acceleration; `scripts/setup.py` cuDNN 8 installation is Windows-only).*

### Windows
```powershell
git clone https://github.com/debpalash/VoiceStudio.git C:\git\VoiceStudio
git -C C:\git\VoiceStudio checkout -b local-fixes c52618af
git -C C:\git\VoiceStudio apply <path-to-dub-anything>\patches\voicestudio-local-fixes.patch
cd C:\git\VoiceStudio; uv sync; uv run --no-sync python scripts/setup.py
uv run --no-sync python -c "import torch; print(torch.cuda.is_available())"   # True
```

`uv sync` installs the CUDA 12.8 PyTorch build on Windows. `scripts/setup.py`
adds the cuDNN 8 libraries that faster-whisper needs next to PyTorch's cuDNN 9.
The patch fixes three bugs. The dubbing transcriber ignored the source-language
hint. OmniVoice ran in float16 on CPU (about 7× slower). And a render that
stopped at a timing overflow threw its takes away: the next attempt
re-synthesized every line instead of only the changed ones. It also adds two
general-purpose voice presets, `echo` for flashbacks and `pa` for
announcements over a public-address system, and lets reverb ring out instead of
stopping dead where the dry take ends. And it lets each dub line carry its own
voice reference (`scripts/dub.py generate --line-ref`), and its own emotion
reference for engines that take one (IndexTTS 2,
`--engine indextts2 --emo-alpha`). For IndexTTS 2.5 it passes a line's speed on as the
engine's duration factor (it was dropped before), and it gives each line a seed,
so the `"american"` voice's fill re-times the same take. See
[directing.md](directing.md#gotchas).

IndexTTS 2.5 is optional (about 12 GB). Install it through the running backend:
`POST http://127.0.0.1:3900/engines/sidecar/indextts2/install`, then poll
`.../install/status`.

## 4. Start the backend

### macOS
From this repo:
```bash
python3 scripts/backend.py start
```
Or directly from VoiceStudio:
```bash
cd ~/python/VoiceStudio && uv run --no-sync uvicorn main:app --app-dir backend --host 127.0.0.1 --port 3900
```

### Windows
From this repo, with VoiceStudio's Python:

```powershell
& C:\git\VoiceStudio\.venv\Scripts\python.exe scripts\backend.py start
```

It starts the backend detached (it keeps running after the terminal or the
agent's session closes), logs to `backend.log` beside the data folder, and
waits until `http://127.0.0.1:3900/health` returns
`{"status":"ok","device":"cuda (...)"}` (or `"device":"mps"` on Apple Silicon). `scripts/backend.py status`, `stop` and
`restart` do what they say; `scripts/dub_anything.py run` starts it when it's down.
Or run it yourself in a terminal you leave open:

```powershell
cd C:\git\VoiceStudio; uv run --no-sync uvicorn main:app --app-dir backend --host 127.0.0.1 --port 3900
```

Restart it after an NVIDIA driver update. A backend that was running across the
update reports "CUDA out of memory" on an empty GPU and falls back to the CPU,
while `/health` still says `cuda`.

## 5. Models (~15 GB)

```powershell
$api = "http://127.0.0.1:3900"
foreach ($r in "k2-fsa/OmniVoice", "Systran/faster-whisper-large-v3", "audio-cpp/audio.cpp-gguf") {
  Invoke-RestMethod -Method Post "$api/models/install" -ContentType application/json -Body (@{repo_id=$r} | ConvertTo-Json) }
Invoke-RestMethod -Method Post "$api/engines/audiocpp/runtime/install"      # Sortformer runtime (optional)
Invoke-RestMethod "$api/models/install/status"                               # progress
```

**pyannote** (speaker diarisation, needed for clips longer than 2 minutes). Accept
the licenses of `pyannote/speaker-diarization-3.1` and
`pyannote/segmentation-3.0` on Hugging Face. Then log in with a read token and
install it:

```powershell
C:\git\VoiceStudio\.venv\Scripts\hf.exe auth login
Invoke-RestMethod -Method Post "$api/models/install" -ContentType application/json -Body '{"repo_id":"pyannote/speaker-diarization-3.1"}'
Invoke-RestMethod -Method Post "$api/engines/diarisation/select" -ContentType application/json -Body '{"engine_id":"pyannote"}'
```

Installing the audio.cpp bundle can switch the active diarisation engine to
Sortformer, so select pyannote again afterwards. Sortformer is limited to 120 s
clips and 4 speakers.

If the audio.cpp runtime install fails with `PermissionError ... Access is denied`
on the final rename, Defender was scanning the new DLLs. Post the install again.

**The `"american"` voice** (optional) needs IndexTTS 2.5 (above) and Chatterbox's
voice converter, which `vc.py` runs in its own uv environment. Clone its source
into the repo's `vendor` folder (git-ignored):

```powershell
git clone --depth 1 https://github.com/resemble-ai/chatterbox vendor\chatterbox
```

The weights (`ResembleAI/chatterbox`, about 1 GB) download into the Hugging Face
cache (`HF_HUB_CACHE`, else `OMNIVOICE_CACHE_DIR`) on the first conversion.

## 6. Check

### macOS
```bash
python3 scripts/dub.py prep <some short clip>.mp4      # prints a Japanese transcript
```

### Windows
```powershell
cd <path-to-dub-anything>
$py = "C:\git\VoiceStudio\.venv\Scripts\python.exe"
& $py scripts\dub.py prep <some short clip>.mp4      # prints a Japanese transcript
```

Five tools run in their own throwaway uv environments, so they can't disturb
VoiceStudio's:
- `separate.py`: audio-separator (on macOS with PyPI torch; on Windows with CUDA 12.8 PyTorch). Its Roformer model
  (about 900 MB) downloads on first use into the model cache (`audio-separator`).
- `second.py`: sherpa-onnx (its models, about 850 MB for Japanese speech,
  1 GB for English, 900 MB for Spanish and 320 MB for Korean, download into the
  model cache on first use).
- `subs.py`: RapidOCR (its Korean recogniser downloads on first use, for a
  Korean source; uses standard onnxruntime on macOS, onnxruntime-gpu on Windows).
- `vc.py`: Chatterbox, which `scripts/dub.py generate` starts itself for the
  `"american"` voice.
- `languages/kana.py`: pykakasi, which `lineqa.py` starts itself to read takes in
  Japanese as kana (dubs into Japanese).

The commands are in [AGENTS.md](../AGENTS.md).

Voice profiles are rebuilt automatically from `work/voices.json` on first use.
Profile ids are machine-specific and cached in `work/profiles.json`.
