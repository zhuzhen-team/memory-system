#!/usr/bin/env bash
# Claude Code SessionEnd hook → memoryd capture
#
# Reads JSON payload from stdin, pipes it to `memoryd capture`.
# Runs in background so CC's exit isn't blocked.
#
# Install: see memoryd/README.md.

set -euo pipefail

# Default to the venv binary because CC's hook process doesn't inherit a
# venv-activated shell. Override via MEMORYD_BIN if you've installed memoryd
# globally or moved the venv.
MEMORYD_BIN="${MEMORYD_BIN:-/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd}"

# Fail early with a clear message if the bin really isn't there
if [[ ! -x "$MEMORYD_BIN" ]]; then
    echo "[$(date -Iseconds)] cc-session-end-hook: memoryd binary not executable at $MEMORYD_BIN" \
        >> "${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs/cc-session-end.log" 2>/dev/null || true
    exit 0  # never block CC's exit
fi

# Buffer stdin (CC pipes it once; we may detach)
PAYLOAD="$(cat)"

# Detach so CC's session-close isn't blocked.
# Errors land in a debug log under the data root.
LOG_DIR="${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/cc-session-end.log"

(
    if echo "$PAYLOAD" | "$MEMORYD_BIN" capture >> "$LOG_FILE" 2>&1; then
        echo "$(date -Iseconds)  ok" >> "$LOG_FILE"
    else
        echo "$(date -Iseconds)  failed (exit $?)" >> "$LOG_FILE"
    fi
) &

disown $! 2>/dev/null || true
exit 0
