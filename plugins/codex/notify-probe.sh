#!/usr/bin/env bash
# Plan 2.5 Phase 1 用户探针：log Codex notify 实际收到什么。
# 临时替换 ~/.codex/config.toml 的 notify field；跑一轮 Codex turn 后
# 把日志粘回给 subagent 用来 design wrapper，然后立刻换回原 notify。
#
# WARNING: 探针运行期间 Codex Computer Use（SkyComputerUseClient）不
# 工作，因为这个脚本不透传调用。Phase 1 完成一次探测即换回。

# NB: 不用 set -e —— probe 必须 best-effort，任何失败都不能阻塞 Codex turn
set -uo pipefail

LOG_DIR="${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/probe"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/notify-probe.log"

{
    printf '=== probe fire at %s ===\n' "$(date -Iseconds)"
    printf 'argv (%d): ' "$#"
    if [[ $# -gt 0 ]]; then
        printf '%q ' "$@"
    fi
    printf '\n\n'

    printf 'stdin (up to 8KB):\n'
    # Read stdin if available, but don't block forever
    if [[ -p /dev/stdin ]] || [[ ! -t 0 ]]; then
        head -c 8192 <&0 || true
        printf '\n[end stdin]\n'
    else
        printf '[no stdin pipe]\n'
    fi
    printf '\n'

    printf 'env (filtered):\n'
    env | grep -iE '^(CODEX|OPENAI|SESSION|TURN|NOTIFY|HOME|USER|PATH)' | sort || true
    printf '=== probe end ===\n\n'
} >> "$LOG_FILE" 2>&1

exit 0
