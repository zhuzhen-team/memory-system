---
title: tri-client-capture-fix（Plan 2.5）设计
date: 2026-05-14
status: 已批准（用户口头通过 brainstorming，跳过 spec review gate per autonomous-first）
related:
  - docs/superpowers/specs/2026-05-09-personal-usage-and-boundary-spec.md
  - docs/superpowers/plans/2026-05-13-tri-client-session-capture.md
role: 设计文档——为 Plan 2.5 实施提供唯一参考；Plan 2.5 plan 与 SDD 实施都引用本文档
---

# Plan 2.5：tri-client-capture-fix 设计

## 0. 这份文档是什么

Plan 2 merge 后实测发现 Codex hooks 上游 runtime 完全不 fire（PreToolUse / PermissionRequest / PostToolUse / PreCompact / PostCompact / SessionStart / UserPromptSubmit / Stop 全部零触发），OpenClaw 2026.5.7 SDK 改成 `definePluginEntry` 不再支持 `api.on('agent_end', ...)`。Plan 2 的代码无法实际工作。本 Plan 2.5 是修复性插入 plan，目标：**让 Codex 和 OpenClaw 两端的 capture 真正在用户日常使用中触发**。

不动 spec（user contract）；不改 Plan 1 的 schema / storage / search / MCP server；只在 capture 通路上做更换。

## 1. 已知的硬事实（empirical，不再 re-investigate）

| 事实 | 验证方式 | 含义 |
|---|---|---|
| codex-cli 0.130.0-alpha.5 hooks engine 任何事件都不 fire | 上次 session 探测所有事件 + GUI 实测 | 不再调试 hooks.json；走 fallback |
| `codex update` 报 "Could not detect installation method" | 实测 | Mac app bundle 没法 CLI 升级；当前 Codex 版本就是终态 |
| `~/.codex/config.toml` `notify` 字段会 fire（SkyComputerUseClient 通过 notify 工作） | 用户日常使用 Codex Computer Use 功能正常 | notify 是可靠 capture 通道；可包装 |
| `~/.codex/memories/rollout_summaries/` 已有 30+ 条 Codex.app 自动写入的会话摘要 `.md` | 本 session ls 实测 | Codex 自己的 session-end artifact；可被 FS-watch 镜像 |
| OpenClaw 2026.5.7 stock 插件用 `definePluginEntry` from `@openclaw/plugin-sdk` | 本 session 读 `/opt/homebrew/lib/node_modules/openclaw/dist/extensions/` 实测 | 必须重写插件 |
| OpenClaw SDK 提供 `api.registerAgentEventSubscription({id, streams, handle})` | 本 session 读 SDK `.d.ts` 实测 | 这就是 turn-end 钩子的现代等价 |
| `~/.openclaw/agents/<agent-id>/sessions/<session-id>.jsonl` 是 OpenClaw 自带的 session log | 本 session 在 SDK 代码里找到 path | FS-watch fallback 的镜像目标 |

## 2. 总体架构

```
CC（不动）
  SessionEnd hook → cc-session-end-hook.sh → memoryd capture --source claude-code

Codex（两条互补通路）
  Path A：~/.codex/config.toml notify field →
    codex-notify-wrapper.sh →
      (1) exec SkyComputerUseClient "$@"  # 透传原 notify target，保 Computer Use 不挂
      (2) fork memoryd capture --source codex-notify  # 实时捕获
  Path B：codex-memories-mirror daemon →
    watchdog 监听 ~/.codex/memories/rollout_summaries/ →
      新 .md → 内容 scope 反推 → SessionMemory(source=codex-rollout) → save_session
  → ~/.codex/hooks.json Stop 条目清掉（永不 fire，避免上游修复 hooks 后双重捕获）

OpenClaw（两条互补通路）
  Path A：openclaw-memoryd-plugin（SDK 重写） →
    definePluginEntry({ id, name, kind, register }) →
    api.registerAgentEventSubscription({ id, streams: [turn-end流], handle }) →
      spawnCapture → memoryd capture --source openclaw
  Path B：openclaw-sessions-mirror daemon →
    watchdog 监听 ~/.openclaw/agents/*/sessions/*.jsonl →
      新文件 → 内容 scope 反推 → SessionMemory(source=openclaw-fs) → save_session

两个 FS-watch daemon 合并到单个 `memoryd mirror --codex --openclaw` 进程：
  → ~/Library/LaunchAgents/com.memoryd.mirror.plist（user LaunchAgent，开机自启）
```

## 3. Source-tag 策略（不做 cross-path 去重）

四类 source tag 共存，互不去重：

- `claude-code`（CC SessionEnd hook，Plan 1）
- `codex-notify`（实时，每 turn）
- `codex-rollout`（事后，每 session 一份摘要）
- `openclaw`（SDK 事件订阅，实时）
- `openclaw-fs`（FS-watch，事后）

**为什么不去重：** Path A 用 notify 给的 session_id，Path B 用 rollout filename 里的 ID——两者命名空间不同，没有稳定 cross-reference 可做去重 key。强行用 timestamp 窗口去重容易误伤同时段多 session。`save_session` 已经按 slug 提供 idempotent 写入（同 slug 重写无害）。

**Cross-path 合并 / fingerprint 去重推迟到 Plan 3**——Plan 3 上 SQLite 索引后，按 (scope_hash, body 前 N 字 hash) 做 merge 是天然成本低，本 Plan 2.5 不引入这层。

## 4. Scope 反推规则（FS-watch 路用）

Codex rollout_summary 和 OpenClaw session log 都不带显式 cwd 字段。反推：

1. 解析文件正文，提取所有形如 `/Users/abble/...` 的绝对路径候选
2. 对每个候选向上遍历，匹配 `~/.codex/config.toml` 的 `[projects."<path>"]` trust_level 表（OpenClaw 路则匹配用户已知项目列表，启动时一次性从 config.toml 读，或允许 env var `MEMORYD_KNOWN_SCOPES` 覆盖）。命中多层时取**最深匹配**（最长前缀胜出），例：候选既匹配 `/Users/abble` 也匹配 `/Users/abble/project-management-personal`，取后者
3. 若恰好命中 1 个 trusted root（去重后） → 用 `scope_hash(root)`
4. 命中 ≥2 个完全不同的 root（不是嵌套关系） / 0 个 → 落到 `~/.local/share/memoryd/scopes/_unscoped/sessions/`，文件不丢；用户后续可手动迁移（`memory move-scope` CLI 推迟到 Plan 3）

## 5. 文件清单

### 新建

```
scripts/codex-notify-probe.sh                         # Phase 1 用户探针
scripts/codex-notify-wrapper.sh                       # Phase 2 真 wrapper
scripts/launchd/com.memoryd.mirror.plist              # daemon LaunchAgent
scripts/openclaw-memoryd-plugin/openclaw.plugin.json  # SDK 插件 manifest
memoryd/src/memoryd/mirror.py                         # 通用 watchdog handler 框架
memoryd/src/memoryd/mirror_codex.py                   # Codex rollout 反推 + 转码
memoryd/src/memoryd/mirror_openclaw.py                # OpenClaw session log 反推 + 转码
memoryd/tests/test_mirror_codex.py
memoryd/tests/test_mirror_openclaw.py
memoryd/tests/test_mirror_scope.py                    # scope 反推共用逻辑
```

### 修改

```
memoryd/pyproject.toml                                # 加 watchdog 依赖
memoryd/src/memoryd/cli.py                            # 加 `mirror` 子命令
scripts/openclaw-memoryd-plugin/package.json          # main 指向 src/index.js + openclaw.extensions
scripts/openclaw-memoryd-plugin/src/index.js          # 新（替换 index.mjs，SDK 入口）
scripts/openclaw-memoryd-plugin/src/payload.js        # 新——把现有 index.mjs 的 pure helpers (buildPayload / materializeTranscript / normalizeMessage / spawnCapture) 抽到这里，保留单测
scripts/openclaw-memoryd-plugin/tests/index.test.mjs  # import 调整到 payload.js
memoryd/README.md                                     # Codex/OpenClaw 章节按新通路重写
docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt  # 追加"Plan 2.5 superseded Plan 2 capture paths"

# 用户配置（用 Python 模块 read-mutate-write + backup ~/.claude/backups/）
~/.codex/config.toml                                  # notify 改指向 codex-notify-wrapper.sh
~/.codex/hooks.json                                   # 删除 Stop 条目（永不 fire）
```

### 删除

```
scripts/openclaw-memoryd-plugin/src/index.mjs   # 被 src/index.js 取代
scripts/codex-stop-hook.sh                       # Codex hooks engine 任何事件都不 fire，死代码；删除（execution log 注明若上游 hooks 修复，重建即可）
```

## 6. 实施阶段划分

**Phase 0 — subagent 全程做（programmatic）**

子任务（按依赖序，每个 subagent 内部 TDD）：
1. `memoryd/src/memoryd/mirror.py` 通用 watchdog handler + scope 反推 + 单测
2. `memoryd/src/memoryd/mirror_codex.py` + 单测（mock fixture 模拟新 rollout_summary 文件落地）
3. `memoryd/src/memoryd/mirror_openclaw.py` + 单测（mock OpenClaw jsonl 文件）
4. CLI `mirror` 子命令 + `pyproject.toml` watchdog 依赖
5. `scripts/codex-notify-probe.sh` + `scripts/codex-notify-wrapper.sh`（含 shellcheck 友好 + mock SkyComputerUseClient 单测）
6. OpenClaw 插件 SDK 重写（保留 payload.js 单测，加 SDK entry 单测——mock `definePluginEntry` 验签名 + mock `registerAgentEventSubscription` 验事件流注册）
7. launchd plist 模板 + install/uninstall 命令
8. README 重写 + execution log

Phase 0 通过判据：
- pytest 全绿（预期 34 + 新增 ~8-10 = 42-44 passed）
- node `--test` 全绿（7 + 新增 ~3 = 10 passed）
- shellcheck 对两个新 sh 脚本 0 warning（如果本机没装 shellcheck，跳过——本 plan 不引入 shellcheck 作为强依赖）

**Phase 1 — 用户批量手测（一次性给出所有步骤，按顺序执行）**

execution log 里写一份操作手册，用户按顺序执行：

1. **Codex notify 探针**
   - subagent 已用 Python read-mutate-write 把 backup 写好、新 notify 指向 `codex-notify-probe.sh`
   - 用户开一次 Codex.app turn（任意 prompt）
   - 用户把 `~/.local/share/memoryd/probe/notify-probe.log` 粘回（含 argv / stdin / env 实际收到什么）
   - 后续 session 我据此 build 真 wrapper 或 pivot 到纯 FS-watch

2. **OpenClaw 插件安装**
   - `cd scripts/openclaw-memoryd-plugin && openclaw plugins install --force .`
   - 用户记下 OpenClaw 给的 entry key
   - `openclaw config set plugins.entries.<KEY>.hooks.allowConversationAccess true`
   - `openclaw config set plugins.entries.<KEY>.hooks.allowPromptInjection false`

3. **launchd daemon 起**
   - subagent 已把 plist 模板渲染好放到 `~/Library/LaunchAgents/`
   - 用户跑 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.memoryd.mirror.plist`
   - 验证 daemon 在跑：`launchctl print gui/$(id -u)/com.memoryd.mirror`

4. **tri-client e2e 暗号验证**
   - 临时空 scope（git init），三端各写一个独特暗号
   - 三端各开新 session 召回另两端的暗号（6 个跨端召回都要成功）
   - 用户回 PASS / FAIL；FAIL 记录症状

5. **清理收尾**
   - 把 `~/.codex/hooks.json` Stop 条目删掉（subagent 用 Python 做）
   - 把 `~/.codex/config.toml` notify 改成真 wrapper（subagent 做）

Phase 1 通过判据：
- 三端 capture log 都有 `ok` 行
- tri-client e2e 6 个跨端召回全 PASS
- 用户没遇到原生功能（Codex Computer Use / OpenClaw 现有插件）回归

## 7. Plan 2.5 完成判据

下面任一未达成即视为未完成：

1. ✅ pytest + node test 全绿（42+/10+）
2. ✅ Codex notify wrapper 真实 fire（或确证 notify 不携带 session 数据并完成 pivot 到纯 FS-watch）
3. ✅ Codex rollout_summary daemon 实时镜像新文件（手测：用户主动开一次 Codex 会话后 60s 内 `~/.local/share/memoryd/scopes/.../sessions/` 出现 `source: codex-rollout` 条目）
4. ✅ OpenClaw 插件 `openclaw plugins list` 中可见且加载无报错
5. ✅ OpenClaw SDK Path A capture 真实 fire（手测：开一轮 OpenClaw 对话，60s 内 `source: openclaw` 条目出现）
6. ✅ tri-client e2e 6 个跨端召回全 PASS
7. ✅ Plan 1/2 已有功能无回归（CC capture 仍正常，memoryd MCP search_memory 工具仍可用）
8. ✅ `~/.codex/config.toml` 和 `~/.codex/hooks.json` 改动都用 Python 模块 read-mutate-write + backup；原有 mcp_servers / trust_level / shell_env / plugins 全部保留

## 8. 不在 Plan 2.5 内（边界）

| 不做 | 推迟到 |
|---|---|
| Cross-path 去重 / merging | Plan 3（SQLite 索引后做 fingerprint） |
| LLM 摘要替代朴素截断 | Plan 3 |
| SQLite 索引 | Plan 3 |
| Windows / Linux daemon 等价物 | Plan 5 |
| `memory move-scope` CLI（清理 `_unscoped`） | Plan 3 |
| 重新调试 Codex hooks 上游 | 永远不（上游修了再说） |
| 让 OpenClaw 直接管 memoryd（用 `registerMemoryCapability`） | 永远不（违反 spec §8 不接管原生记忆机制） |

## 9. 风险与回退

| 风险 | 触发条件 | 回退 |
|---|---|---|
| notify wrapper 破坏 Codex Computer Use | wrapper 没正确 `exec` SkyComputerUseClient | 把 `~/.codex/config.toml` notify 改回原值（subagent 已 backup） |
| OpenClaw `registerAgentEventSubscription` stream 名字猜错 | 实测 handler 0 触发 | 退到只 FS-watch（删插件，依赖 Path B） |
| FS-watch daemon 内存泄漏 / CPU 飙 | launchd 日志 / Activity Monitor | `launchctl bootout` 拆 daemon；手动 `memoryd mirror` 排查 |
| Scope 反推误判 | 用户验 `_unscoped` 桶有内容 | 看反推日志（记到 `~/.local/share/memoryd/logs/mirror.log`），调启发式 |
| 用户配置文件 backup 失败 | Python 写文件异常 | subagent 实施时强制先 backup 再写；写失败直接 abort 不破坏原文件 |

## 10. 与上游 spec 的关系

本 Plan 2.5 不引入 spec 没说过的功能边界。所有改动都是 spec §3 "捕获姿态 = 全自动" 的实现细节调整。spec §3 / §6 / §8 不变。

## 11. 变更记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-05-14 | 初版 | Plan 2 merge 后实测 Codex hooks 完全不 fire、OpenClaw SDK 变了，需要修复性 plan |
