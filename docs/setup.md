# Setup (Windows + NVIDIA)

dubrama needs a running [VoiceStudio](https://github.com/debpalash/VoiceStudio)
backend, its models, and ffmpeg. The paths below are the ones used so far; any
others work.

## 1. Tools

- An NVIDIA GPU with ≥ 6 GB of VRAM (VoiceStudio's default voice engine). On a
  16 GB RTX 4090 laptop everything runs at once. `nvidia-smi` should work.
- NVIDIA driver ≥ 610 if you want `split.py --encoder h264_nvenc` (current
  FFmpeg builds need NVENC API 13.1). Otherwise use the default x264.
- `winget install Git.Git astral-sh.uv Gyan.FFmpeg`, then open a new terminal.

## 2. Keep models and data out of AppData

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

dubrama's scripts find VoiceStudio's jobs through `OMNIVOICE_DATA_DIR`, so it
must be set in any shell that runs them.

## 3. VoiceStudio, pinned, with local fixes

```powershell
git clone https://github.com/debpalash/VoiceStudio.git C:\git\VoiceStudio
git -C C:\git\VoiceStudio checkout -b local-fixes c52618af
git -C C:\git\VoiceStudio apply <path-to-dubrama>\voicestudio-local-fixes.patch
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
voice reference (`dub.py generate --line-ref`), and its own emotion reference
for engines that take one (IndexTTS 2, `--engine indextts2 --emo-alpha`). See [directing.md](directing.md#gotchas).

IndexTTS 2.5 is optional (about 12 GB). Install it through the running backend:
`POST http://127.0.0.1:3900/engines/sidecar/indextts2/install`, then poll
`.../install/status`.

## 4. Start the backend

Leave this running in its own terminal:

```powershell
cd C:\git\VoiceStudio; uv run --no-sync uvicorn main:app --app-dir backend --host 127.0.0.1 --port 3900
```

It's ready when `http://127.0.0.1:3900/health` returns
`{"status":"ok","device":"cuda (...)"}`.

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

```powershell
cd <path-to-dubrama>
$py = "C:\git\VoiceStudio\.venv\Scripts\python.exe"
& $py dub.py prep <some short clip>.mp4      # prints a Japanese transcript
```

Four tools run in their own throwaway uv environments, so they can't disturb
VoiceStudio's:
- `separate.py`: audio-separator with the CUDA 12.8 PyTorch. Its Roformer model
  (about 900 MB) downloads on first use into `%OMNIVOICE_CACHE_DIR%\audio-separator`.
- `second.py`: sherpa-onnx.
- `subs.py`: RapidOCR.
- `vc.py`: Chatterbox, which `dub.py generate` starts itself for the
  `"american"` voice.

The commands are in [AGENTS.md](../AGENTS.md).

Voice profiles are rebuilt automatically from `work/voices.json` on first use.
Profile ids are machine-specific and cached in `work/profiles.json`.
