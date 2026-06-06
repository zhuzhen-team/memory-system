#!/usr/bin/env bash
# Codex notify wrapper:
#   1. exec the original notify target (Codex Computer Use) transparently
#   2. fork memoryd capture in background with whatever payload was provided
#
# `~/.codex/config.toml` notify is rewritten to:
#   notify = [".../codex-notify-wrapper.sh", "turn-ended"]
# All arguments after the path are passed through. The original notify
# target path is read from `~/.codex/.memoryd-notify-state.json`
# (written by `memoryd setup swap-codex-notify` on first swap). CODEX_NOTIFY_ORIGINAL
# env var is honored as an override if the state file is missing.
#
# Failures NEVER block Codex; we always exit 0 unless the original notify
# returns nonzero (we honor its exit code).

set -uo pipefail

LOG_DIR="${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/codex-notify.log"

# Resolve original notify target: state file (preferred) → env var (fallback)
STATE_FILE="${CODEX_NOTIFY_STATE:-$HOME/.codex/.memoryd-notify-state.json}"
ORIGINAL=""
if [[ -f "$STATE_FILE" ]] && command -v python3 >/dev/null 2>&1; then
    ORIGINAL="$(python3 -c "
import json, sys
try:
    with open('$STATE_FILE') as f:
        d = json.load(f)
    orig = d.get('original', [])
    if isinstance(orig, list) and orig:
        print(orig[0])
except Exception:
    pass
" 2>/dev/null)"
fi
if [[ -z "$ORIGINAL" ]]; then
    ORIGINAL="${CODEX_NOTIFY_ORIGINAL:-}"
fi

# Buffer stdin (we may need it for both the original target and memoryd).
# Cap at 1MB to avoid OOM if something unusual is piped; real notify
# payloads are tiny (~kB).
PAYLOAD=""
if [[ ! -t 0 ]]; then
    PAYLOAD="$(head -c 1048576 || true)"
fi

# 1. Fork memoryd capture FIRST (best-effort, never blocks Codex).
#
# Order matters: capture used to run AFTER the original notify returned, but
# the 2026-06-05 rebuild of SkyComputerUseClient can hang forever, which
# blocked the wrapper before it ever reached the capture step — sessions
# silently stopped being captured and hung notify chains piled up in ps.
# Capture must not depend on any downstream notify target.
MEMORYD_BIN="${MEMORYD_BIN:-/Users/abble/memory-system/memoryd/.venv/bin/memoryd}"
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

# 2. Call the original notify target (Computer Use, etc.) under a watchdog.
# 30s is generous for a notification ping; a hung target gets killed so we
# don't leak processes (and we exit 0 — its notification is best-effort too).
ORIGINAL_EXIT=0
if [[ -n "$ORIGINAL" ]] && [[ -x "$ORIGINAL" ]]; then
    if [[ -n "$PAYLOAD" ]]; then
        printf '%s' "$PAYLOAD" | "$ORIGINAL" "$@" &
    else
        "$ORIGINAL" "$@" </dev/null &
    fi
    ORIG_PID=$!
    for _ in $(seq 1 60); do
        kill -0 "$ORIG_PID" 2>/dev/null || break
        sleep 0.5
    done
    if kill -0 "$ORIG_PID" 2>/dev/null; then
        kill "$ORIG_PID" 2>/dev/null
        printf '%s  original notify timed out after 30s; killed (pid %s)\n' \
            "$(date -Iseconds)" "$ORIG_PID" >> "$LOG_FILE"
    else
        wait "$ORIG_PID"
        ORIGINAL_EXIT=$?
    fi
fi

exit "$ORIGINAL_EXIT"
