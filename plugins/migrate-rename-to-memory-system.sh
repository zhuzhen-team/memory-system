#!/usr/bin/env bash
#
# 一次性迁移脚本：把仓库本地路径从 project-management-personal 改成 memory-system
# 之后更新所有引用旧路径的配置文件（~/.claude.json / ~/.claude/settings.json
# / launchd plists / ~/.codex/config.toml）。
#
# 用法（在你 `mv ~/project-management-personal ~/memory-system` 之后跑）：
#   cd ~/memory-system
#   bash scripts/migrate-rename-to-memory-system.sh
#
# 脚本是幂等的：跑两遍不会错，第二遍报告 "nothing to do"。
# 所有改动前都备份到 ~/.claude/backups/ 时间戳目录。
#
set -euo pipefail

OLD_PATH="/Users/abble/project-management-personal"
NEW_PATH="/Users/abble/memory-system"
BACKUP_DIR="$HOME/.claude/backups/migrate-$(date +%Y%m%d-%H%M%S)"

PY=python3

cyan()  { printf "\033[36m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }

# --- 0. 健全性检查 ----------------------------------------------------------

if [[ "$(pwd)" != "$NEW_PATH" ]]; then
  red "must run from $NEW_PATH (current: $(pwd))"
  red "did you 'mv ~/project-management-personal ~/memory-system && cd ~/memory-system' first?"
  exit 2
fi

if [[ -d "$OLD_PATH" ]]; then
  red "$OLD_PATH still exists; mv it first to $NEW_PATH"
  exit 2
fi

mkdir -p "$BACKUP_DIR"
cyan "backup dir: $BACKUP_DIR"

# --- 1. 更新 ~/.claude.json (mcpServers.memoryd.command) -------------------

CLAUDE_JSON="$HOME/.claude.json"
if [[ -f "$CLAUDE_JSON" ]]; then
  cp "$CLAUDE_JSON" "$BACKUP_DIR/claude.json"
  cyan "patching ~/.claude.json ..."
  $PY - <<EOF
import json, sys
from pathlib import Path

old = "$OLD_PATH"
new = "$NEW_PATH"
p = Path("$CLAUDE_JSON")
d = json.loads(p.read_text())
servers = d.get("mcpServers", {})
changes = 0
for name, cfg in servers.items():
    cmd = cfg.get("command", "")
    if old in cmd:
        cfg["command"] = cmd.replace(old, new)
        changes += 1
    env = cfg.get("env", {})
    for k, v in env.items():
        if isinstance(v, str) and old in v:
            env[k] = v.replace(old, new)
            changes += 1
tmp = p.with_suffix(".json.tmp")
tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False))
tmp.replace(p)
print(f"  ~/.claude.json: {changes} substitutions")
EOF
else
  yellow "~/.claude.json not found; skipping"
fi

# --- 2. 更新 ~/.claude/settings.json (hooks.SessionEnd command) ------------

SETTINGS="$HOME/.claude/settings.json"
if [[ -f "$SETTINGS" ]]; then
  cp "$SETTINGS" "$BACKUP_DIR/settings.json"
  cyan "patching ~/.claude/settings.json ..."
  $PY - <<EOF
import json
from pathlib import Path

old = "$OLD_PATH"
new = "$NEW_PATH"
p = Path("$SETTINGS")
d = json.loads(p.read_text())
changes = 0
hooks = d.get("hooks", {})
for event, entries in hooks.items():
    if not isinstance(entries, list):
        continue
    for entry in entries:
        for h in entry.get("hooks", []):
            cmd = h.get("command", "")
            if old in cmd:
                h["command"] = cmd.replace(old, new)
                changes += 1
tmp = p.with_suffix(".json.tmp")
tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False))
tmp.replace(p)
print(f"  ~/.claude/settings.json: {changes} substitutions")
EOF
else
  yellow "~/.claude/settings.json not found; skipping"
fi

# --- 3. launchd plists ------------------------------------------------------

LAUNCH_DIR="$HOME/Library/LaunchAgents"
PLISTS=(
  "com.memoryd.mirror.plist"
  "com.memoryd.decay-sweep.plist"
  "com.memoryd.weekly-digest.plist"
)
UID_=$(id -u)

for plist in "${PLISTS[@]}"; do
  P="$LAUNCH_DIR/$plist"
  if [[ -f "$P" ]]; then
    cp "$P" "$BACKUP_DIR/$plist"
    label="${plist%.plist}"
    cyan "patching $plist (bootout + replace + bootstrap) ..."
    # stop
    launchctl bootout "gui/$UID_/$label" 2>/dev/null || true
    # patch
    sed -i.bak "s|$OLD_PATH|$NEW_PATH|g" "$P"
    rm -f "${P}.bak"
    # reload (best-effort; on failure user can re-run bootstrap manually)
    if launchctl bootstrap "gui/$UID_" "$P" 2>/dev/null; then
      green "  $plist patched + reloaded"
    else
      yellow "  $plist patched but bootstrap failed; run manually:"
      yellow "    launchctl bootstrap gui/$UID_ $P"
    fi
  fi
done

# --- 4. ~/.codex/config.toml notify field -----------------------------------

CODEX_TOML="$HOME/.codex/config.toml"
if [[ -f "$CODEX_TOML" ]]; then
  if grep -q "$OLD_PATH" "$CODEX_TOML"; then
    cp "$CODEX_TOML" "$BACKUP_DIR/config.toml"
    cyan "patching ~/.codex/config.toml ..."
    sed -i.bak "s|$OLD_PATH|$NEW_PATH|g" "$CODEX_TOML"
    rm -f "${CODEX_TOML}.bak"
    green "  ~/.codex/config.toml patched"
  else
    cyan "~/.codex/config.toml: no references to $OLD_PATH; skipping"
  fi
fi

# --- 5. CC auto-memory 目录搬家（保留旧的 + 复制到新 encoded path）---------

OLD_ENCODED="$HOME/.claude/projects/-Users-abble-project-management-personal"
NEW_ENCODED="$HOME/.claude/projects/-Users-abble-memory-system"

if [[ -d "$OLD_ENCODED/memory" ]]; then
  if [[ -d "$NEW_ENCODED/memory" ]]; then
    yellow "$NEW_ENCODED/memory already exists; not overwriting"
  else
    cyan "copying CC auto-memory $OLD_ENCODED → $NEW_ENCODED ..."
    mkdir -p "$NEW_ENCODED"
    cp -R "$OLD_ENCODED/memory" "$NEW_ENCODED/"
    green "  CC auto-memory copied（旧目录保留作为备份）"
  fi
fi

# --- 6. memoryd data root（默认 ~/.local/share/memoryd）path-derived scope_hash ----
# 注意：scope_hash 来自工作目录的 resolved path 派生。旧 scope_hash 是基于
# /Users/abble/project-management-personal 算的。改路径后新 scope_hash 不同，
# 这是 spec §3 跨平台 caveat 在本地路径变化时同样适用。
# 旧 scope 数据在 ~/.local/share/memoryd/scopes/<old_hash>/ 还在；新会话会落
# 新 scope_hash 目录。
# 解决：要么用户在 ~/memory-system 跑一次 memoryd capture 之后手动 move-scope（v2
# 待加），要么在新 path 里加 symlink 让 resolve_scope_root 回到旧路径。
# 这里只打印提示，不自动做（避免破坏现有 scope 数据）。
OLD_HASH=$($PY -c "import hashlib; print(hashlib.sha1(b'$OLD_PATH').hexdigest()[:12])")
NEW_HASH=$($PY -c "import hashlib; print(hashlib.sha1(b'$NEW_PATH').hexdigest()[:12])")
yellow ""
yellow "memoryd scope_hash 变化（路径派生）："
yellow "  old: $OLD_HASH (~/.local/share/memoryd/scopes/$OLD_HASH/)"
yellow "  new: $NEW_HASH (~/.local/share/memoryd/scopes/$NEW_HASH/)"
yellow ""
yellow "如果你想保留旧 scope 的所有记忆延续，跑："
yellow "  mv ~/.local/share/memoryd/scopes/$OLD_HASH ~/.local/share/memoryd/scopes/$NEW_HASH"
yellow "  cd $NEW_PATH && uv --directory memoryd run memoryd rebuild-index"

# --- 7. 总结 ---------------------------------------------------------------

green ""
green "迁移完成。备份在 $BACKUP_DIR"
green ""
green "下一步建议："
green "  1. 重启你的 IDE / Claude Code 让它认到新路径"
green "  2. 跑 cd $NEW_PATH/memoryd && uv run pytest 2>&1 | tail -3 验证"
green "  3. 跑一轮 CC turn 确认 SessionEnd hook 仍正常 capture"
green "  4. 如果想保留旧 scope 数据，按上面提示 mv 旧 scope_hash 目录"
