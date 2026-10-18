#!/bin/zsh
# Wrapper script for daily scheduled CTV News Toronto Cantonese dubbing.

# Load user profile / environment variables (such as GEMINI_API_KEY)
if [ -f "$HOME/.zshrc" ]; then
    source "$HOME/.zshrc" > /dev/null 2>&1
fi

export PATH="/opt/homebrew/bin:/opt/homebrew/opt/python@3.11/libexec/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO_DIR="/Users/billylo/python/dub-anything"
cd "$REPO_DIR" || exit 1

LOG_DIR="$REPO_DIR/work/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/auto_dub_$(date +'%Y%m%d').log"

echo "=== Starting Daily CTV Dubbing at $(date) ===" >> "$LOG_FILE"
python3 "$REPO_DIR/scripts/auto_dub_ctv.py" "$@" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
echo "=== Finished Daily CTV Dubbing with exit code $EXIT_CODE at $(date) ===" >> "$LOG_FILE"

exit $EXIT_CODE
