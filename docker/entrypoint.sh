#!/usr/bin/env bash
set -eo pipefail

echo "=========================================================="
echo " Starting Dub Anything Cloud Run Container"
echo " Time: $(date -u)"
echo "=========================================================="

# Check GPU hardware
if command -v nvidia-smi &> /dev/null; then
    echo "[*] NVIDIA GPU Detected:"
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader || true
else
    echo "[!] WARNING: nvidia-smi not available. Running on CPU or non-standard driver."
fi

# Set directories
export OMNIVOICE_DATA_DIR="${OMNIVOICE_DATA_DIR:-/app/data}"
export OMNIVOICE_CACHE_DIR="${OMNIVOICE_CACHE_DIR:-/models}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-/models/hf_cache}"
export VOICESTUDIO_DIR="${VOICESTUDIO_DIR:-/app/VoiceStudio}"
export PATH="/app/VoiceStudio/.venv/bin:/root/.local/bin:/usr/local/bin:${PATH}"

mkdir -p "$OMNIVOICE_DATA_DIR" "$OMNIVOICE_CACHE_DIR" "$HF_HUB_CACHE" /app/work/logs

# Fast local caching of heavy PyTorch models from GCSFuse mount
if [ -d "/models/models--openai--whisper-large-v3-turbo" ] && [ ! -d "/root/.cache/huggingface/hub/models--openai--whisper-large-v3-turbo" ]; then
    mkdir -p /root/.cache/huggingface/hub
    echo "[*] Pre-caching Whisper Large-v3 Turbo to local SSD in background..."
    cp -r /models/models--openai--whisper-large-v3-turbo /root/.cache/huggingface/hub/ 2>/dev/null &
fi

BACKEND_PID=""
if [ "${SKIP_BACKEND:-0}" != "1" ]; then
    # Start VoiceStudio Backend in background
    echo "[*] Starting VoiceStudio backend on port 3900..."
    cd "$VOICESTUDIO_DIR"

if [ -f "$VOICESTUDIO_DIR/.venv/bin/python" ]; then
    VS_PY="$VOICESTUDIO_DIR/.venv/bin/python"
else
    VS_PY="python3"
fi

$VS_PY -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 3900 > /app/work/logs/backend.log 2>&1 &
BACKEND_PID=$!
echo "[*] VoiceStudio process launched with PID: $BACKEND_PID"

# Healthcheck loop
echo "[*] Awaiting VoiceStudio backend readiness at http://127.0.0.1:3900/health..."
READY=0
for i in {1..60}; do
    if curl -s http://127.0.0.1:3900/health | grep -q '"status":"ok"'; then
        READY=1
        echo "[*] VoiceStudio backend is online and healthy!"
        curl -s http://127.0.0.1:3900/health || true
        echo ""
        break
    fi
    sleep 2
done

if [ "$READY" -ne 1 ]; then
    echo "[!] ERROR: VoiceStudio backend failed to start within 120s. Dumping log:"
    cat /app/work/logs/backend.log || true
    kill -9 "$BACKEND_PID" || true
    exit 1
fi
fi

# Switch back to app directory
cd /app/dub-anything

# Execute commanded entrypoint
echo "=========================================================="
echo " Executing: $*"
echo "=========================================================="
"$@"
EXIT_CODE=$?

echo "=========================================================="
echo " Pipeline finished with exit code: $EXIT_CODE"
echo "=========================================================="

# Cleanup backend
kill "$BACKEND_PID" 2>/dev/null || true

exit $EXIT_CODE
