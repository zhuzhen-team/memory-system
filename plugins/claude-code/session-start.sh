#!/usr/bin/env bash
# Claude Code SessionStart hook -> memoryd inject
#
# CC pipes our stdout into `additionalContext` for the model. We must:
#   1. produce useful markdown if memoryd has data,
#   2. never block or fail the CC startup (graceful no-op on any error),
#   3. write all diagnostics to a log file under the data root.
#
# Install via `memoryd setup install-cc-session-start-hook` — see
# plugins/claude-code/README or docs/integrations/claude-code.md.

# POSIX-safe: works under macOS bash 3.2, zsh, and Linux bash.
set -u

LOG_DIR="${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs"
LOG_FILE="$LOG_DIR/cc-session-start.log"
mkdir -p "$LOG_DIR" 2>/dev/null || true

# Direct stderr to log; stdout is reserved for CC (additionalContext).
exec 2>>"$LOG_FILE"

# Best-effort cd into the project root so `--scope=auto` resolves
# correctly. CLAUDE_PROJECT_DIR is set by CC when launching the hook.
cd "${CLAUDE_PROJECT_DIR:-$HOME}" 2>/dev/null || cd "$HOME" 2>/dev/null || true

# Resolve memoryd binary. Prefer explicit override, then venv, then PATH.
MEMORYD_BIN="${MEMORYD_BIN:-}"
if [ -z "$MEMORYD_BIN" ]; then
    for cand in \
        "$HOME/memory-system/memoryd/.venv/bin/memoryd" \
        "$(command -v memoryd 2>/dev/null)"
    do
        if [ -n "$cand" ] && [ -x "$cand" ]; then
            MEMORYD_BIN="$cand"
            break
        fi
    done
fi

if [ -z "$MEMORYD_BIN" ] || [ ! -x "$MEMORYD_BIN" ]; then
    # No memoryd installed -> emit nothing; CC starts normally.
    echo "[$(date -u +%FT%TZ)] memoryd binary not found; skipping inject" >&2
    exit 0
fi

# 5s ceiling — protect CC startup if memoryd hangs on a corrupted DB.
# `timeout` is GNU on Linux, gtimeout via coreutils on macOS; fall back
# to a backgrounded wait+kill if neither is available.
run_with_timeout() {
    if command -v timeout >/dev/null 2>&1; then
        timeout 5s "$@"
        return $?
    fi
    if command -v gtimeout >/dev/null 2>&1; then
        gtimeout 5s "$@"
        return $?
    fi
    # Portable fallback for old macOS: spawn + sleep watchdog.
    "$@" &
    pid=$!
    (sleep 5 && kill -TERM "$pid" 2>/dev/null) &
    watchdog=$!
    wait "$pid" 2>/dev/null
    rc=$?
    kill -TERM "$watchdog" 2>/dev/null || true
    return $rc
}

run_with_timeout "$MEMORYD_BIN" inject \
    --scope=auto \
    --max-chars=1500 \
    --top-entities=8 \
    --recent=5 || {
    # Whatever happened, do not break CC startup.
    echo "[$(date -u +%FT%TZ)] memoryd inject failed (rc=$?); emitting empty" >&2
    echo ""
}

exit 0
