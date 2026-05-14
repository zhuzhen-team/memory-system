#!/usr/bin/env bash
# Codex notify wrapper:
#   1. exec the original notify target (Codex Computer Use) transparently
#   2. fork memoryd capture in background with whatever payload was provided
#
# `~/.codex/config.toml` notify is rewritten to:
#   notify = [".../codex-notify-wrapper.sh", "turn-ended"]
# All arguments after the path are passed through. The original notify
# target path is stored in CODEX_NOTIFY_ORIGINAL env (set by setup CLI)
# so we know what to invoke.
#
# Failures NEVER block Codex; we always exit 0 unless the original notify
# returns nonzero (we honor its exit code).

set -uo pipefail

LOG_DIR="${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/codex-notify.log"

ORIGINAL="${CODEX_NOTIFY_ORIGINAL:-}"

# Buffer stdin (we may need it for both the original target and memoryd)
PAYLOAD=""
if [[ ! -t 0 ]]; then
    PAYLOAD="$(cat || true)"
fi

# 1. Transparently call the original notify target (Computer Use, etc.)
ORIGINAL_EXIT=0
if [[ -n "$ORIGINAL" ]] && [[ -x "$ORIGINAL" ]]; then
    if [[ -n "$PAYLOAD" ]]; then
        printf '%s' "$PAYLOAD" | "$ORIGINAL" "$@"
    else
        "$ORIGINAL" "$@" </dev/null
    fi
    ORIGINAL_EXIT=$?
fi

# 2. Fork memoryd capture (best-effort, never blocks Codex)
MEMORYD_BIN="${MEMORYD_BIN:-/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd}"
if [[ -x "$MEMORYD_BIN" ]]; then
    (
        # Build a minimal JSON payload from argv + cwd guess; the CLI is
        # tolerant of unknown / missing keys (plan 1 behavior). If argv
        # contains JSON on stdin already, prefer that.
        if [[ -n "$PAYLOAD" ]] && printf '%s' "$PAYLOAD" | python3 -c 'import json,sys; json.loads(sys.stdin.read())' >/dev/null 2>&1; then
            # stdin was JSON — use it verbatim
            printf '%s' "$PAYLOAD" | "$MEMORYD_BIN" capture --source codex-notify >> "$LOG_FILE" 2>&1
        else
            # synthesize from argv
            SESSION_ID="codex-notify-$(date +%s)-$$"
            python3 -c "
import json, os, sys
print(json.dumps({
    'session_id': '$SESSION_ID',
    'transcript_path': '',
    'cwd': os.environ.get('PWD', '/'),
    'argv': sys.argv[1:],
}))
" -- "$@" | "$MEMORYD_BIN" capture --source codex-notify >> "$LOG_FILE" 2>&1
        fi
        printf '%s  capture done\n' "$(date -Iseconds)" >> "$LOG_FILE"
    ) &
    disown $! 2>/dev/null || true
fi

exit "$ORIGINAL_EXIT"
