# ==============================================================================
# Dub Anything Cloud Run GPU Container (Ubuntu 22.04 + NVIDIA CUDA 12.8)
# ==============================================================================
FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    OMNIVOICE_DATA_DIR=/models/data \
    OMNIVOICE_CACHE_DIR=/models \
    HF_HUB_CACHE=/models/hf_cache \
    VOICESTUDIO_DIR=/app/VoiceStudio \
    PATH="/root/.cargo/bin:${PATH}"

# 1. System libraries, FFmpeg, Python 3.11, and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ffmpeg \
    libsndfile1 \
    libgl1 \
    libglib2.0-0 \
    lsof \
    procps \
    ca-certificates \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    nodejs \
    npm \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set default python3 to 3.11
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# 2. Install modern uv package manager, yt-dlp, and firebase-tools
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
RUN pip3 install --no-cache-dir --upgrade yt-dlp
RUN npm install -g firebase-tools

WORKDIR /app

# 3. Clone VoiceStudio pinned at commit c52618af
RUN git clone https://github.com/debpalash/VoiceStudio.git /app/VoiceStudio \
    && cd /app/VoiceStudio \
    && git checkout -b local-fixes c52618af

# Copy and apply local patches
COPY patches/voicestudio-local-fixes.patch /app/VoiceStudio/
RUN cd /app/VoiceStudio && git apply voicestudio-local-fixes.patch

# Create virtual environment and install PyTorch with CUDA 12.8
RUN cd /app/VoiceStudio && \
    uv venv .venv --python 3.11 && \
    uv pip install --index-url https://download.pytorch.org/whl/cu128 \
        torch==2.8.0 torchvision torchaudio && \
    uv pip install -r requirements.txt || true && \
    uv pip install uvicorn fastapi requests numpy scipy soundfile librosa onnxruntime-gpu rapidocr sherpa-onnx pykakasi huggingface_hub

# 4. Copy Dub Anything codebase
COPY . /app/dub-anything
WORKDIR /app/dub-anything

# Ensure scripts and entrypoint are executable
RUN chmod +x docker/entrypoint.sh scripts/run_daily_ctv.sh

ENTRYPOINT ["/app/dub-anything/docker/entrypoint.sh"]
CMD ["python3", "scripts/auto_dub_ctv.py"]
