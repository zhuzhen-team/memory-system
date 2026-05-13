#!/usr/bin/env bash
# Codex Stop hook → memoryd capture (source=codex)
#
# Reads JSON payload from stdin (event passed by Codex hooks engine),
# pipes it to `memoryd capture --source codex`. Runs in background so
# Codex's turn-end isn't blocked.
#
# Install: see memoryd/README.md (Codex section).

set -euo pipefail

MEMORYD_BIN="${MEMORYD_BIN:-/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd}"

if [[ ! -x "$MEMORYD_BIN" ]]; then
    echo "[$(date -Iseconds)] codex-stop-hook: memoryd binary not executable at $MEMORYD_BIN" \
        >> "${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs/codex-stop.log" 2>/dev/null || true
    exit 0  # never block Codex's turn-end
fi

PAYLOAD="$(cat)"

LOG_DIR="${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/codex-stop.log"

(
    if printf '%s' "$PAYLOAD" | "$MEMORYD_BIN" capture --source codex >> "$LOG_FILE" 2>&1; then
        printf '%s  ok\n' "$(date -Iseconds)" >> "$LOG_FILE"
    else
        printf '%s  failed (exit %s)\n' "$(date -Iseconds)" "$?" >> "$LOG_FILE"
    fi
) &

disown $! 2>/dev/null || true
exit 0
