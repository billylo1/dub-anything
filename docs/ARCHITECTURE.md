# Dub Anything Architecture

Dub Anything is an automated AI dubbing pipeline that turns machine transcripts into directed dialogue, fits speech into character windows, and renders cloned voices.

## System Architecture

```mermaid
flowchart TD
    subgraph Inputs & Discovery
        SRC[Source Video / YouTube] --> INTAKE[Intake & Video Split]
    end

    subgraph Speech & Separation Pipeline
        INTAKE --> SEP[audio-separator\nRoformer Vocals/BGM]
        INTAKE --> ASR[faster-whisper\nSpeech Recognition & Word Timestamps]
        INTAKE --> DIAR[pyannote / WeSpeaker\nSpeaker Diarization]
    end

    subgraph Translation & Directing Engine
        ASR & DIAR --> DRAFT[Draft Script]
        DRAFT --> LLM[Gemini 2.5 Flash\nBroadcast Translation & Pacing Constraint]
        LLM --> FIT[core.fit_rows\nPacing & OVER Prevention]
    end

    subgraph Voice Synthesis Backend
        FIT --> VS[VoiceStudio REST API :3900]
        VS --> OMNI[OmniVoice / IndexTTS 2.5\nNeural Voice Cloning]
    end

    subgraph Assembly & Delivery
        OMNI --> ASM[assemble.py\nFFmpeg Audio Remux]
        ASM --> DEL[deliver.py\nMulti-track Video & Subtitles]
        DEL --> PUB[Firebase Hosting / Cloud Storage\nvideo.billylo.ca]
    end
```

## Runtime Environments

Dub Anything supports three deployment topologies:

### 1. Local macOS (Apple Silicon Metal / MPS)
- **Acceleration**: Apple Metal (`mps` device) via PyTorch Metal backend.
- **Backend**: Electron-bundled or source VoiceStudio running on `127.0.0.1:3900`.
- **Paths**: `~/Library/Application Support/OmniVoice`, `~/Library/Caches/OmniVoice`.
- **Automation**: macOS `launchd` plist (`ca.billylo.auto-dub-ctv.plist`).

### 2. Local / Workstation Linux (NVIDIA CUDA)
- **Acceleration**: NVIDIA CUDA 12.8 / cu128 PyTorch wheels.
- **Backend**: Headless Uvicorn process managed by `scripts/backend.py`.
- **Paths**: `~/.local/share/OmniVoice`, `~/.cache/OmniVoice`.

### 3. Google Cloud Run Jobs (Headless Serverless GPU)
- **Container**: Ubuntu 22.04 LTS + CUDA 12.8 (`Dockerfile`).
- **Hardware**: 1 $\times$ NVIDIA L4 GPU (24 GB VRAM), 4 vCPUs, 16 GiB RAM.
- **Model Storage**: Persistent Cloud Storage bucket (`gs://videos-billylo-models`) mounted via Cloud Storage FUSE at `/models`.
- **Secrets**: Injected via Google Cloud Secret Manager (`GEMINI_API_KEY`).
- **Scheduling**: Google Cloud Scheduler triggering the Cloud Run Job daily.
- **Publishing**: Direct deployment to Firebase Hosting (`--project videos-billylo`).
