---
title: CLI 命令
keywords: CLI, memoryd, 子命令, 命令清单
---

# CLI 命令：全清单

源码：[memoryd/src/memoryd/cli.py](https://github.com/EthanQC/memory-system/blob/main/memoryd/src/memoryd/cli.py)

`memoryd --help` 列全部一级子命令。下面按用途分组速查。

## 写入 / 内部 hook 用

| 命令 | 作用 |
|---|---|
| `memoryd capture --source=<tag>` | 读 stdin JSON payload，落 `sessions/<id>.md`；自动 fork DURA + KG 分析 |
| `memoryd analyze-session <slug>` | 单独跑一遍 DURA / 实体抽取（capture 已自动调，手工重试用） |
| `memoryd mirror --codex / --openclaw` | 启 watchdog 守护，监听 Codex / OpenClaw 文件 |
| `memoryd mirror --codex --once` | 单次扫描，不进入 watch 循环（CI / 诊断用） |
| `memoryd rebuild-index` | 清空 SQLite，扫所有 markdown 重建 |

## 读取

| 命令 | 作用 |
|---|---|
| `memoryd search "<query>"` | 全文 + 向量 + 实体加权混合搜索 |
| `memoryd search "<q>" --type=decision --scope=<hash> --limit=20 --json` | 全参数 |
| `memoryd list` | 列最近 50 条 |
| `memoryd list --type=decision --scope=<hash> --limit=100 --json` | 过滤版 |
| `memoryd show <slug>` | 输出 frontmatter + body |
| `memoryd show <slug> --scope=<hash>` | 显式 scope |

## 治理

| 命令 | 作用 |
|---|---|
| `memoryd digest` | 文本视图（promotions + duplicates + decayed） |
| `memoryd digest --json` | JSON（脚本用） |
| `memoryd digest --notify` | 同时弹原生通知 + SMTP 邮件 |
| `memoryd digest --tui` | 启动 textual 交互界面 |
| `memoryd promote <id>` | 批准 pending promotion → 真写 .md |
| `memoryd merge --keep <slug> --drop <slug> <slug>` | 合并重复 |
| `memoryd delete <slug> [--force]` | 删除（不可逆，默认 y/N 确认） |
| `memoryd decay-sweep` | 跑一遍衰减状态机（cron 已自动） |
| `memoryd audit` | 看审计链 |
| `memoryd audit --scope=<hash> --since=<iso> --event-type=<t> --json` | 过滤 |
| `memoryd audit --verify [-v]` | 重算 prev_hash 链，损坏时 exit 1 |

## 敏感作用域

| 命令 | 作用 |
|---|---|
| `memoryd mark-sensitive <path>` | 加密该 scope 全部 .md，密钥进 keyring |
| `memoryd unmark-sensitive <path>` | 反操作 |
| `memoryd grant <path> --duration once / session / task --task <name>` | 授权 |
| `memoryd revoke <path> [--task <name>]` | 撤销 |

duration 三档：

- `once` — 90 秒
- `session` — 8 小时
- `task --task <name>` — 直到 `revoke --task <name>`

## 跨设备同步

| 命令 | 作用 |
|---|---|
| `memoryd sync export` | 路径 A：增量 mirror 本地 → sync dir |
| `memoryd sync export --scope=<hash> --dry-run` | 过滤 + 预览 |
| `memoryd sync export --auto` | 仅在 `sync.enabled && sync.auto_export_on_session_end` 时跑（hook 用） |
| `memoryd sync import` | 反向；自动 rebuild-index |
| `memoryd sync status` | per-scope counts + `_conflicts` 数量 |
| `memoryd sync status --json` | JSON |
| `memoryd set-passphrase` | 进 passphrase 模式后初始化 |

## 一次性 import

```bash
memoryd import claude-md <path>             # ~/.claude/CLAUDE.md 等
memoryd import auto-memory <dir>            # ~/.claude/projects/<proj>/memory/
memoryd import agents-md <path>             # ~/.codex/AGENTS.md
memoryd import mcp-memory-service <path>    # mcp-memory-service 导出的 memories.json
```

共享参数：

- `--scope=<hash>` — 显式落到某 scope（默认 cwd 派生）
- `--dry-run` — 不写，仅输出 plan
- `--force` — 覆盖同 slug
- `--source-tag=<custom>` — 覆盖 frontmatter source

## 配置

```bash
memoryd config show
memoryd config set llm.provider anthropic
memoryd config set llm.model    claude-haiku-4-5
memoryd config set sync.dir     ~/Dropbox/memoryd
```

值会自动 coerce 成 int / float / bool / str。

## setup 子命令组

`memoryd setup <subcmd>` —— 用户配置管理 + 三端挂接 + cron。

### Codex 相关

| 命令 | 作用 |
|---|---|
| `memoryd setup swap-codex-notify --to probe / wrapper / original` | 切换 notify 模式 |
| `memoryd setup remove-codex-stop-hook` | 清理 ~/.codex/hooks.json 里死的 Stop 条目 |

### launchd / 守护

| 命令 | 作用 |
|---|---|
| `memoryd setup install-launchd-mirror` | 渲染并安装 LaunchAgent plist（macOS） |
| `memoryd setup uninstall-launchd-mirror` | 反操作 |

### cron（跨平台）

| 命令 | 作用 |
|---|---|
| `memoryd setup install-cron --decay` | 装 daily decay-sweep |
| `memoryd setup install-cron --digest` | 装 weekly digest |
| `memoryd setup install-cron --all` | 全装 |
| `memoryd setup uninstall-cron --all` | 反操作 |

### Claude Code hook

| 命令 | 作用 |
|---|---|
| `memoryd setup install-cc-hook` | 写 ~/.claude/settings.json 的 SessionEnd hook |
| `memoryd setup install-memory-searcher` | 拷 sub-agent 模板到 ~/.claude/agents/ |
| `memoryd setup install-memory-searcher --force` | 覆盖 |
| `memoryd setup install-memory-searcher --target=./.claude/agents/` | 项目级 |

### 一键安装

```bash
memoryd setup auto-install
```

按当前平台依次装：cron + cc-hook。

## 知识图谱（kg）

```bash
memoryd kg entities [--scope=<hash>] [--type=<t>] [--top=20] [--window-days=30] [--json]
memoryd kg memories-about <entity_name_or_id> [--types=session,decision,...] [--json]
memoryd kg evolution <entity_name_or_id> [--json]
memoryd kg subgraph <entity_name_or_id> [--depth=2] [--out=<path>] [--format=cytoscape|json]
memoryd kg conflicts [--scope=<hash>] [--json]
```

详见 [知识图谱：让记忆从条目堆变成图](../architecture/knowledge-graph.md)。

## 画像（profile）

```bash
memoryd profile show [--max-chars=2000]            # 当前 identity.md 节选
memoryd profile history [--limit=20]               # 历次快照
memoryd profile diff --from=<n> --to=<m>           # 两版 unified diff
memoryd profile rewrite [--dry-run] [--window-days=7] [--max-words=800] [--on-event]
memoryd profile report --month=<YYYY-MM> [--dry-run] [--regenerate]
memoryd profile trends [--window-days=7] [--json]
```

详见 [画像自学习](../architecture/profile-learning.md)。

## HANDOFF（项目级交接快照）

跟用户级的 `identity.md`（私有、跨项目）不同，HANDOFF.md 是**项目级**的工作交接快照
——写在项目根，可以 git commit、跟代码一起分发给同事 / 下一个 AI 会话。

```bash
memoryd handoff write [--cwd=<path>] [--out=<path>] [--snapshot] \
                      [--scope=auto] [--global] \
                      [--window-days=7] [--no-llm] [--dry-run] [--force]
memoryd handoff read  [--cwd=<path>] [--date=YYYY-MM-DD]
memoryd handoff list  [--cwd=<path>]
```

- **write**：从最近 N 天 decision / warning / session / identity 抽信号，调 LLM
  按 6 区块（TL;DR / 当前状态 / 下一步 / 关键决策 / 文件结构 / 已知坑）生成 HANDOFF.md。
  - 默认写 `<cwd>/HANDOFF.md`，已存在时拒绝覆盖（要 `--force`）
  - `--snapshot` 写 `HANDOFF-YYYY-MM-DD.md` 不动 canonical 版本（适合"工作日结束 + 留底"）
  - `--out=<path>` 写到任意路径（适合"快速预览到 /tmp / 桌面"，不会触发 SessionStart 自动注入）
  - `--no-llm` 走纯结构化 fallback（无 LLM 费用，离线可用，但缺凝练，需手动调整）
  - `--scope=auto`（默认）= 用 cwd 派生的 scope_hash；`--global` = 跨 scope 聚合
- **read**：打印 cwd 的 HANDOFF.md；`--date` 读历史快照。
- **list**：列项目根所有 HANDOFF 系列文件。

SessionStart 注入自动会读 cwd/HANDOFF.md 加到 `additionalContext`，所以新会话开场
就有项目状态了，不用 Claude Code 自己 grep。详见 [HANDOFF 教程](../tutorials/handoff.md)。

## Web Dashboard

```bash
memoryd web                       # 随机端口；stderr 输出 token URL
memoryd web --port=8088           # 显式端口
memoryd web --no-browser          # 不自动 open（CI / SSH）
```

详见 [Web 仪表板](web-dashboard.md)。

## MCP server

```bash
memoryd-mcp                       # stdio transport（默认）
memoryd-mcp --transport http --port 8766
MEMORYD_MCP_ADMIN=1 memoryd-mcp   # 启用 6 个 admin 工具
memoryd-mcp --admin               # 等价
memoryd-mcp --verbose             # debug 日志
```

详见 [MCP 工具](mcp-tools.md)。

旧 server（向后兼容，仅 `search_memory` 单工具）：

```bash
memoryd-server
```

## 环境变量

| 变量 | 用途 |
|---|---|
| `MEMORYD_DATA_ROOT` | 数据根路径覆盖（默认 `~/.local/share/memoryd`） |
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `OPENAI_API_KEY` | OpenAI provider |
| `MEMORYD_AUTH_INTERACTIVE` | 设为 1，sensitive scope 没 grant 时弹 `/dev/tty` 4 选项 prompt |
| `MEMORYD_MCP_ADMIN` | 1 = 启用 6 个 admin MCP 工具 |
| `MEMORYD_MCP_TRANSPORT` | `stdio` / `http`（命令行 --transport 优先） |
| `MEMORYD_MCP_PORT` | HTTP port（命令行 --port 优先） |
| `MEMORYD_MCP_HOST` | HTTP bind host |
| `MEMORYD_MASTER_PASSPHRASE` | passphrase 模式临时值（优先于 keyring） |
| `MEMORYD_SMTP_PW` | SMTP 邮件 digest 密码 |
| `MEMORYD_INJECT_BUDGET` | session-start 注入 token 预算（默认 500） |

## 退出码

- `0` 成功
- `1` 普通错误
- `2` 参数错误 / stdin 为空 / JSON 无效
- 其他 `subprocess` 透传
