# memoryd

Personal memory governance MCP server. Part of `project-management-personal`.

**Status:** v0.7.0 — Web dashboard + TUI (plan 7 of 8)

Currently supports:
- macOS / Linux / Windows
- **Claude Code, Codex, and OpenClaw three clients share a single scope** (was: Claude Code only)
- Multi-device sync via raw .md mirror to user-configured sync dir (Plan 6)
- Local web dashboard + textual digest TUI (Plan 7)
- Session capture only (decisions/preferences/promotions in plan 3)
- Plain Markdown storage (encryption in plan 4)
- ripgrep-based search (semantic search in plan 3)
- 敏感作用域加密（mark-sensitive，AES-256-GCM + macOS Keychain，Plan 4）

## Install (macOS)

Prereqs: Python 3.11+, [`uv`](https://github.com/astral-sh/uv), `ripgrep` (`brew install ripgrep`).

```bash
cd /path/to/project-management-personal/memoryd
uv venv
uv pip install -e ".[dev]"
```

## Wire into Claude Code

Add the following to `~/.claude/settings.json` (merge with existing keys):

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/project-management-personal/scripts/cc-session-end-hook.sh"
          }
        ]
      }
    ]
  }
}
```

Add the `memoryd` entry to `~/.claude.json` (the flat file at your home root — **not** `~/.claude/.mcp.json`, which CC ignores for user-level servers). Merge under the existing top-level `mcpServers` key using Python to avoid corrupting other entries (tokens, other servers):

```python
import json
from pathlib import Path

path = Path.home() / ".claude.json"
with open(path) as f:
    d = json.load(f)

d.setdefault("mcpServers", {})
d["mcpServers"]["memoryd"] = {
    "command": "/path/to/project-management-personal/memoryd/.venv/bin/memoryd-server",
    "args": [],
    "env": {
        "MEMORYD_DATA_ROOT": "/Users/<you>/.local/share/memoryd"
    }
}

tmp = path.with_suffix(".json.tmp")
with open(tmp, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
tmp.replace(path)
```

Restart Claude Code. Run `/mcp` and verify `memoryd` appears with `search_memory` tool.

## Wire into Codex（Plan 2.5 双通路）

> Codex.app 的 hooks engine 当前版本对所有事件零触发（已实测）；Plan 2.5
> 改走两条互补通路：notify wrapper 实时捕获 + 文件系统监听 rollout_summary。
> 旧的 `scripts/codex-stop-hook.sh` 已删除。

### 1. 备份并替换 notify 字段（实时通路）

```bash
# 完整切到 wrapper（先做一遍 probe 才知道 notify 真实 schema；
# 详见下面 Phase 1 手册）
/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd setup swap-codex-notify --to wrapper
```

子命令自动：
- 把 `~/.codex/config.toml` 备份到 `~/.claude/backups/`
- 用 Python tomllib 读，正则替换 `notify` 字段保留其他 keys
- 把原 notify target 存到 `~/.codex/.memoryd-notify-state.json`，便于 `--to original` 回滚

### 2. 删除死的 Stop hook 条目

```bash
memoryd setup remove-codex-stop-hook
```

### 3. 启动 FS-watch daemon（事后通路）

```bash
memoryd setup install-launchd-mirror
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.memoryd.mirror.plist
launchctl print gui/$(id -u)/com.memoryd.mirror  # 验证 daemon 在跑
```

Daemon 监听 `~/.codex/memories/rollout_summaries/`；Codex.app 每个 session 结束后
会自己往那里写一份 summary `.md`，daemon 把它转码成 memoryd 的 `source=codex-rollout`
记忆条目。

### 4. 验证

跑一轮 Codex.app turn，检查：

```bash
# 实时通路日志
tail ~/.local/share/memoryd/logs/codex-notify.log

# FS-watch 通路日志
tail ~/.local/share/memoryd/logs/mirror.stderr.log

# 新生成的 memoryd 条目
find ~/.local/share/memoryd/scopes -name "*.md" -newer /tmp -ls
```

## Wire into OpenClaw（Plan 2.5 双通路）

> OpenClaw 2026.5.7 的插件 SDK 是 `definePluginEntry` + `registerAgentEventSubscription`，
> 不再支持旧的 `api.on('agent_end', ...)`。Plan 2.5 重写插件入口；同时让 launchd
> daemon 监听 `~/.openclaw/agents/*/sessions/` 作 fallback。

### 1. 安装插件

```bash
cd /Users/abble/project-management-personal/scripts/openclaw-memoryd-plugin
openclaw plugins install --force .
openclaw plugins list | grep memoryd-openclaw
```

### 2. 授权对话访问

```bash
# 用 install 输出的 entry key（通常就是 memoryd-openclaw）
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowConversationAccess true
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowPromptInjection false
```

### 3. FS-watch daemon

Codex 那一步装的 launchd plist 已经同时覆盖 OpenClaw 路径（`--codex --openclaw` 双开）；无需额外操作。

### 4. 验证

跑一轮 OpenClaw turn，检查：

```bash
tail ~/.local/share/memoryd/logs/openclaw-events.log  # SDK 通路诊断
find ~/.local/share/memoryd/scopes -newer /tmp -name "*.md" -ls
```

`source: openclaw`（SDK 实时）或 `source: openclaw-fs`（FS-watch）。

## Layout

```
src/memoryd/
  schema.py          # Pydantic Markdown frontmatter schema
  scope.py           # cwd → scope_hash (git-root preferred)
  storage.py         # save/load/list session markdowns
  search.py          # ripgrep-based search
  server.py          # FastMCP server with search_memory tool
  cli.py             # `memoryd capture` / `mirror` / `setup` subcommands
  mirror.py          # watchdog handler framework + _unscoped bucket（Plan 2.5）
  mirror_codex.py    # Codex rollout_summary 转码（source=codex-rollout，Plan 2.5）
  mirror_openclaw.py # OpenClaw session jsonl 转码（source=openclaw-fs，Plan 2.5）
  setup.py           # 用户配置管理：notify swap / hooks 清理 / launchd 安装（Plan 2.5）

tests/
  test_schema.py
  test_scope.py
  test_storage.py
  test_search.py
  test_cli.py
  test_server.py
  test_mirror.py           # Plan 2.5
  test_mirror_codex.py     # Plan 2.5
  test_mirror_openclaw.py  # Plan 2.5
  test_setup.py            # Plan 2.5
```

Memory data root (default `~/.local/share/memoryd`):

```
scopes/
  <scope_hash>/
    sessions/
      2026-05-09-<session-id>.md
  _unscoped/                  # 反推不到 scope 时兜底（Plan 2.5）
    sessions/...
logs/
  cc-session-end.log
  codex-notify.log            # Plan 2.5 实时通路
  openclaw-events.log         # Plan 2.5 OpenClaw SDK 事件
  mirror.stdout.log           # Plan 2.5 launchd daemon
  mirror.stderr.log
probe/
  notify-probe.log            # Plan 2.5 Phase 1 探针
```

## Run tests

```bash
uv run pytest -v
```

## Long-term memory governance (Plan 3)

### LLM 配置

Plan 3 用 LLM 做 4 准则候选筛选。默认 Anthropic Claude Haiku 4.5。

```bash
# 1. 把 API key 写到 shell rc（~/.zshrc / ~/.bashrc）
export ANTHROPIC_API_KEY=sk-ant-xxx

# 2.（可选）改 provider / model
memoryd config show
memoryd config set llm.model claude-sonnet-4-6
```

如果不配 API key，capture 仍正常工作，只是不会自动跑 DURA → 不生成 promotion 候选。可以手动跑 `memoryd analyze-session <slug>` 重试。

### 类型扩展

会话 capture 走 `source` tag 路径（Plan 1-2.5）；长期记忆走 6 种类型：

| 类型 | 何时用 | TTL |
|---|---|---|
| session | 自动捕获摘要 | 90 天 → decay |
| decision | 用户明确决策 | 永不过期 |
| preference | 工作偏好 | 永不过期 |
| fact | 客观事实 | 永不过期 |
| playbook | 操作流程 | 永不过期 |
| warning | 踩过的坑 | 永不过期 |

智能体在会话中说"记一下这个决策"→ 调 `promote_to_long_term` 或 `record_long_term` MCP 工具自动写。

### Digest 复盘

每周（默认）跑：

```bash
memoryd digest                  # 文本视图
memoryd digest --json           # JSON（脚本调用）
```

三栏：
- **候选提升**：DURA ≥ 0.6 的 LLM 推荐
- **重复合并**：fingerprint 相同的条目对
- **TTL 到期**：进 dim / soft-forgotten 的提醒

合并：
```bash
memoryd merge --keep <good-slug> --drop <bad-slug-1> <bad-slug-2>
```

### Decay / soft-forget

session 90 天没召回 → `dim`；再 30 天 → `soft-forgotten`（默认 search 不返回）；再 90 天 → 物理迁到 `forgotten/`。手动跑：

```bash
memoryd decay-sweep
```

Plan 5 会加 cron 自动每天跑。

### MCP 工具清单（7/12）

| # | 工具 | 用途 |
|---|---|---|
| 1 | search_memory | 全文 + trigger + type 过滤 |
| 2 | promote_to_long_term | 把 session 段提升为长期记忆 |
| 3 | record_long_term | 直接写长期记忆（不通过 session） |
| 4 | list_by_type | 列单类型 |
| 5 | get_memory | 取详情 |
| 6 | list_promotions | 列待审批候选 |
| 7 | merge_duplicates | 合并 |

## Sensitive scopes (Plan 4)

> 把某个目录标为敏感后，里面所有记忆自动 AES-256-GCM 加密；任何 MCP
> 工具读取前必须有有效 grant；所有访问都进 JSONL append-only 审计日志。

### Mark a scope sensitive

```bash
memoryd mark-sensitive ~/scopes/finance
```

子命令一气：
- 写 `~/scopes/finance/.memoryd-sensitive` marker 文件（人类可读）
- 生成 AES-256 key 进 macOS Keychain（service `memoryd-scope-key`，account 是 scope_hash）
- 把 `~/.local/share/memoryd/scopes/<scope_hash>/*.md` 全部 encrypt → `.md.enc`，删原 .md
- SQLite memories.scope_sensitive=1

子目录自动继承——`~/scopes/finance/sub` 也算敏感。不能在敏感作用域内再开非敏感子作用域（spec §3）。

### Grant access

```bash
# Three durations:
memoryd grant ~/scopes/finance --duration once       # 90 秒
memoryd grant ~/scopes/finance --duration session    # 8 小时
memoryd grant ~/scopes/finance --duration task --task my-deep-work
memoryd revoke ~/scopes/finance --task my-deep-work
```

### Agent workflow

智能体调 search_memory / get_memory 等 → server 检测 scope sensitive → 没 grant 直接 raise `AuthorizationRequired` tool error → 智能体应当（a）放弃读、降级响应，或（b）调 `request_sensitive_read` 工具显式请求授权。

设 `MEMORYD_AUTH_INTERACTIVE=1` 后 server 会经 `/dev/tty` 弹 4 选项 prompt（仅 CLI client 如 CC 有效；Codex.app GUI 不行）。

### Audit log

```bash
memoryd audit                                                  # 全部事件表格
memoryd audit --scope=<scope_hash>                             # 按 scope 过滤
memoryd audit --since=2026-05-01T00:00:00+00:00                # 时间窗
memoryd audit --event-type=access_denied                       # 只看拒绝事件
memoryd audit --json                                           # JSON 输出
```

`~/.local/share/memoryd/audit/audit.jsonl` 一行一事件，含 prev_hash sha256 链——篡改单行会让后面所有行的链断掉。

### Limitations of Plan 4

- macOS only：Keychain 后端，Plan 5 加 Windows DPAPI / Linux Secret Service
- 跨设备：sensitive scope 在新机需要重新 mark + 重新生成密钥（密钥不进 Plan 6 同步盘）
- Web UI 审计页推迟到 Plan 7

## Cross-platform install (Plan 5)

memoryd v0.5.0 起 macOS / Linux / Windows 三平台都可用。
- 加密：keyring 自动选 backend（Keychain / Credential Manager / Secret Service）
- Daemon 自启：launchd / systemd user / Task Scheduler
- Digest 通知：原生 GUI + 可选 SMTP

### One-shot install

```bash
memoryd setup auto-install
```

按平台依次：
- 装 cron（decay 03:00 daily + weekly digest Mon 09:00）
- 写 CC SessionEnd hook（Python wrapper）

### Granular control

```bash
memoryd setup install-cron --decay
memoryd setup install-cron --digest
memoryd setup install-cron --all
memoryd setup install-cc-hook
```

### 反操作

```bash
memoryd setup uninstall-cron --all
```

### SMTP digest（可选）

`~/.config/memoryd/config.toml`：

```toml
[notify.smtp]
enabled = true
host = "smtp.gmail.com"
port = 587
use_tls = true
from = "you@gmail.com"
to = "you@gmail.com"
username = "you@gmail.com"
password_env = "MEMORYD_SMTP_PW"
```

`export MEMORYD_SMTP_PW=<app-password>` 后 `digest --notify` 同时发邮件。

### Limitations

- Linux：需 secret-service daemon（gnome-keyring / KeePassXC）
- Windows：BurntToast 未装时降级 msg.exe
- 老 Linux systemd：可能需 `loginctl enable-linger <user>` 让 user timer 在登录前跑

## Multi-device sync (Plan 6)

memoryd v0.6.0 起多电脑同步通过用户自配同步盘（坚果云 / iCloud / Dropbox）镜像裸 .md 文件。Markdown 是 source of truth；SQLite index / audit log / grants / Keychain 密钥都不进同步盘。

### 配置

`~/.config/memoryd/config.toml`：

```toml
[sync]
enabled = true
dir = "~/Library/CloudStorage/Dropbox/memoryd"   # 用户选自己的同步盘
auto_export_on_session_end = true
auto_import_on_session_start = true

[sensitive]
key_source = "passphrase"      # 默认 "random"（Plan 4 行为）；passphrase 启用 Plan 6 跨设备
kdf_iters = 600000
```

### 命令

```bash
memoryd sync export                 # 增量 mirror local → sync dir
memoryd sync export --scope=<hash>  # 仅指定 scope
memoryd sync export --dry-run

memoryd sync import                 # 反向：sync dir → local；自动 rebuild-index
memoryd sync status                 # per-scope counts + _conflicts
memoryd sync status --json          # JSON

memoryd set-passphrase              # 进 passphrase 模式后用
```

### 冲突解决

`memoryd sync import` 检测到本地和 sync dir 同 slug 但 fingerprint 不同时：
- 本地版备份到 `~/.local/share/memoryd/scopes/_conflicts/<slug>-<fp8>.md`
- sync 版上位
- 用户后续在 digest 复盘 / 手动 merge

### Passphrase 模式

敏感作用域跨设备同步的 trade-off：

- **random**（Plan 4 default）：每个 scope 一 32B 随机 key，本地 OS keyring 存。换机不能解原 `.md.enc`，用户在新机重 mark-sensitive。
- **passphrase**（Plan 6 opt-in）：用户在所有机器跑 `memoryd set-passphrase` 输入同一短语；32B key 用 PBKDF2-HMAC-SHA256(passphrase, salt=scope_hash, iters=600k) 推导。`.md.enc` 在所有机器都可解。

切换：

```bash
memoryd config set sensitive.key_source passphrase   # 待 memoryd config set 子命令完成；
                                                      # 暂时直接编辑 config.toml
export MEMORYD_MASTER_PASSPHRASE='your-12-char-or-more'  # CI / 临时；优先于 keyring
memoryd set-passphrase                                # 持久化到 OS keyring
```

### 跨平台 scope_hash 一致性 caveat

scope_hash 派生自 resolved 路径。macOS `/Users/<u>/projects/foo` vs Linux `/home/<u>/projects/foo` → 不同 scope_hash → 同一逻辑项目在新机器算作新 scope。

v1 解决方案：保持机器间 home dir 布局一致（如 symlink `/Users/<u>` → `/home/<u>`），或在新机器手动 `memoryd move-scope <old_hash> <new_hash>`（v2 自动从 git remote 派生）。

### Limitations

- SQLite index.db 永不进同步盘（spec §4.7 #26 避免 WAL 锁损坏）
- Audit log / grants / Keychain 密钥不进同步盘（安全边界）
- passphrase 忘记 → 所有 `.md.enc` 永久无法解（无 recovery）
- 跨平台路径差异需手动 align（v2 fix）

## Web dashboard + TUI (Plan 7)

memoryd v0.7.0 加本机轻量 Web Dashboard（浏览-only）+ textual digest TUI（approve/reject）。spec §4.3 #11 + §4.8 #30-32。

### 启动 Web Dashboard

```bash
memoryd web                       # 随机端口；stderr 输出含 token URL
memoryd web --port=8088           # 显式端口
memoryd web --no-browser          # 不自动 open（CI / SSH 场景）
```

启动后 stderr 出一行：

```
memoryd web on http://127.0.0.1:<port>/?token=<256-bit-token>
```

把整个 URL（含 ?token=）复制到浏览器；token 不复制无法访问。重启 memoryd web token 变。

### Web 路由

| 路径 | 用途 |
|---|---|
| `/` | 主页：搜索框 + 最近 20 条记忆 |
| `/memories` | list；`?type=` / `?scope=` / `?page=` 过滤 |
| `/memories/{slug}` | detail；sensitive scope 一律 403 |
| `/search?q=` | HTMX fragment 全文搜索；不含 sensitive scope |
| `/audit` | audit log 表格；`?scope=` / `?since=` / `?event_type=` |
| `/digest` | pending promotions 列表（read-only） |
| `/healthz` | 公共探活，不需 token |

Web 仅浏览不可编辑（spec §6）；编辑走 CLI / 直接编辑 Markdown。

### 安全模型

- 绑定 `127.0.0.1`（loopback；非 0.0.0.0）
- 256-bit token 每次启动生成；不持久化
- middleware 接受 query string `?token=` / cookie `memoryd_token` / `Authorization: Bearer <token>` 三选一
- 拒绝时返回 401 JSON；无登录页
- 不支持 HTTPS（本机；v2 视需要加）
- Sensitive scope：list 显 🔒 占位；detail 直接 403；search 排除其内容

### Digest TUI

```bash
memoryd digest --tui              # 启动 textual 交互界面
```

键盘：
- `a` Approve all pending
- `r` Reject highlighted
- `s` Skip
- `q` Quit

依赖：textual ≥ 0.40；macOS Terminal / iTerm / Windows Terminal 都兼容。老 Windows Console 可能渲染异常——建议升级。

### Basic Memory schema 对齐

Frontmatter 加 3 个新字段（全 optional + default empty，向后兼容）：

- `tags: [str]` — Plan 1 已存；Basic Memory 标签语义
- `category: str | None` — 顶层归类（如 `decisions/architecture`）
- `observations: [str]` — 小条目状的事实附加（Basic Memory observations）

`relations` Plan 3 已存，与 Basic Memory 同名复用。这一对齐为 v2 接 Obsidian / Basic Memory 留路（spec §10 #2）。

### Limitations

- Web 仅浏览不可编辑（v2 视使用频率决定加）
- 不支持 HTTPS / 多用户（spec §6）
- textual 老 Windows Console 渲染异常（用 Windows Terminal）
- Sensitive scope detail 永 403（不会因为有 grant 就让 Web 看到内容；用 CLI `memoryd show`）

## Limitations of v1.0-α

- No encryption. Don't put secrets here.
- macOS only. Multi-machine sync in plan 6.

See `docs/superpowers/plans/2026-05-09-v1-alpha-walking-skeleton.md` for full plan history; subsequent plans live alongside it.
