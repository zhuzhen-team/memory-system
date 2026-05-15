---
title: 个人使用视角的记忆 / 上下文系统 v1 规格
date: 2026-05-09
status: draft（待用户 review）
related:
  - docs/roadmap.md
  - docs/detailed-plan.md
  - 记忆系统设计/05-方案B深度设计.md
  - 记忆库/README.md
role: 用户契约层（user contract）——所有后续 sub-design（memsearch fork、memoryd 设计、敏感授权 UX、同步方案）从这里引用，不再重新讨论"我作为用户要什么"
---

# 个人使用视角的记忆 / 上下文系统 v1 规格

## 0. 这份文档是什么

这是 v1 的 **用户契约**——记录"我作为用户期望系统给我什么、不给我什么、在什么边界里运行"。

它不是架构文档。架构决策（具体用哪些开源仓、SQLite schema、MCP 传输层、daemon 自启实现）放在 detailed-plan.md 和后续 sub-design。这里只回答**"用户视角能做什么、看到什么、不做什么"**。

后续每个 sub-design（memsearch fork 改造、memoryd MCP 设计、同步方案、敏感授权 UX、Web Dashboard）都必须满足这份文档列出的功能与边界。如果某个 sub-design 与这份文档冲突，先回来修这份文档，不要单方面绕过。

## 1. 系统一句话定位

**一个本地优先的、跨三端的、自动接住所有会话的、按目录隔离的个人记忆中枢；它默认安静，需要时随手能查，敏感的会先问，每周提醒收拾一下，永远不试图替换三端工具原生的工作方式。**

## 2. 用户画像

- **使用场景**：多领域——软件工程 + 写作 + 研究 + 生活规划
- **使用工具**：Claude Code、Codex、OpenClaw 三端日常并用
- **使用设备**：Mac + Windows + Linux 多机交替
- **首要痛点**：
  1. 跨会话失忆——开新会话重新解释项目背景
  2. 多端割裂——CC 里聊的 Codex/OpenClaw 不知道
  3. 上下文找不到——记得讨论过但找不在哪
- **次要关切**：决策反复（被列为可接受但非首要）
- **数据观**：本地优先，本人完全所有，反对云端 SaaS / 多人共享同库

## 3. 核心立场清单

这一节是后续所有 sub-design 的**指导原则**。每条都已经在 brainstorm 中明确确认。

| 维度 | 立场 | 含义 |
|---|---|---|
| 捕获姿态 | **全自动** | 三端任意会话结束后，系统自动生成摘要入工作记忆，无需用户确认 |
| 召回姿态 | **默认静默** | 不在 SessionStart 注入；不在新会话弹"上次聊到什么"；智能体判断需要才调用 memory MCP 工具 |
| 作用域隔离 | **严格按目录** | 工作目录 = 作用域单元；跨域不互访；生活规划等场景需建独立目录（如 `~/scopes/life`）。作用域根识别：优先用最近 `.git` 父目录；若无则用用户在 `memory init <path>` 显式声明的根；子目录全部继承父作用域 |
| 敏感识别 | **目录级声明** | 不做内容关键词扫描；用户标记目录为敏感（`memory mark-sensitive ~/scopes/finance`），里面所有记忆加密存储；**子目录全部继承敏感属性**，不能在敏感作用域内开非敏感子作用域 |
| 敏感读取 | **必须授权** | 智能体读敏感记忆前，必须出示授权对话框；授权粒度：仅本次 / 本会话 / 本任务；全部留审计日志 |
| 长期记忆 | **完整治理** | v1 包含类型 / 作用域 / 来源 / TTL / 决策取代关系 / 提升候选筛选；不只做"工作记忆 + 搜索" |
| 复盘节奏 | **周期推送** | 每周或每月（用户配置）系统主动生成 digest；用户 5–10 分钟批量批准 / 拒绝 / 合并；不接受逐会话审核 |
| 三端体验 | **保留原生** | 不改变 CC/Codex/OpenClaw 的启动方式；不接管 CLAUDE.md / AGENTS.md / auto-memory；只做加法不做减法 |
| MCP 工具预算 | **≤ 12 个** | memoryd MCP server 暴露的工具数严格 ≤ 12，三端常驻约 3–4k token；管理动作走 CLI |
| 数据存储 | **Markdown + SQLite** | Markdown 是 source of truth（可读、可手编、Git 友好）；SQLite 仅作索引可重建 |

## 4. v1 必须实现的功能（32 项）

### 4.1 自动后台（用户不管）

1. 三端任意会话结束自动生成摘要入工作记忆
2. CC 的 PreCompact hook 触发"压缩前抢救关键决策"
3. 决策、踩坑、偏好按 **4 准则** 自动过滤值得提升的候选：
   - **D**urability 持久性（3 个月后仍有意义）
   - **U**niqueness 独特性（不在已有记忆里）
   - **R**etrievability 可检索性（有明确触发词）
   - **A**uthority 权威性（用户明确决策 / 事实）
4. 工作记忆按工作目录自动分作用域，跨域不互访

### 4.2 智能体自然交互

5. 智能体决定何时召回，**主上下文零侵占**——通过 `memory-searcher` sub-agent 模板（CC 端可选装，model: haiku，tools: Read+Grep，输出严格 < 500 token JSON）
6. 三端用同一份记忆，跨工具不再重新解释项目背景
7. 对话内可直接说"记下这条 / 标为决策 / 设为项目偏好"——立刻提升进长期记忆
8. 对话内可直接说"这段不要记 / 删了它 / 找上次关于 X 的讨论"

### 4.3 主动控制（CLI / TUI）

9. CLI：`memory search <query>` / `memory list` / `memory show <id>` / `memory delete` / `memory promote` / `memory merge`
10. CLI：`memory digest` 手动触发摘要审核
11. TUI：交互式浏览 / 审核界面（参考 engram TUI 设计）
12. CLI：`memory audit --scope=<x> [--since=<time>]` 查看敏感访问审计日志

### 4.4 周期复盘

13. 每周（默认）系统主动生成 digest，桌面通知
14. Digest 内容三类：候选提升 / 重复合并 / TTL 到期
15. 跳过本周也可以——TTL 自带 decay + compression + soft-forgetting（参考 mcp-memory-service 5 阶段中的可借鉴部分）

### 4.5 敏感保护

16. 目录级敏感声明：`memory mark-sensitive <path>`
17. 敏感目录全部加密存储；解密钥匙本地（macOS Keychain / Windows DPAPI / Linux Secret Service 各自适配）
18. 智能体读敏感前出现授权对话框；用户选授权范围
19. 授权范围：仅本次 / 本会话 / 本任务（直到用户说"切换任务"）；用户拒绝时智能体降级为"无敏感上下文"继续工作，不阻塞主任务
20. 所有敏感访问（包括拒绝事件）留审计日志；可查询、不可篡改（追加只写日志）

### 4.6 跨工具协调

21. Claude Code、Codex、OpenClaw **不需要换启动方式**——继续 `claude` / `codex` / OpenClaw 原命令
22. 各自原生记忆（CLAUDE.md / AGENTS.md / auto-memory）**完全不动**
23. memoryd MCP server 是三端的统一接口；三端各自的 hook / 插件机制做异步捕获

### 4.7 数据所有权与跨设备

24. 全部本地存储，**绝不联网**（除非用户主动调外部模型 API）
25. Markdown 是 source of truth，SQLite 仅索引可重建
26. **多电脑同步**：SessionEnd 自动 export `memories.json` 到同步目录（坚果云 / iCloud / Dropbox 用户自选），SessionStart 自动 import；SQLite 不进同步盘（避免 WAL 锁损坏）
27. **平台支持 v1 三端**：macOS LaunchAgent + Windows Task Scheduler + Linux systemd timer 自启
28. **导入旧记忆**：v1 提供 `memory import` 子命令支持：
    - `memory import claude-md <path>` — 把现有 CLAUDE.md 内容导入
    - `memory import auto-memory <path>` — 把 `~/.claude/projects/<proj>/memory/` 导入
    - `memory import agents-md <path>` — 把 Codex AGENTS.md 导入
    - `memory import mcp-memory-service <path>` — 把 mcp-memory-service 的 `memories.json` 导入
    - 全部是单向导入，不做双向同步
29. 导出能力：JSON / Markdown 全量导出 + Git 友好的目录结构

### 4.8 浏览界面

30. **命令行 + TUI**（默认主路）
31. **轻量 Web Dashboard**——本机 `127.0.0.1:<port>`：记忆列表、标签过滤、全文搜索、统计概览、敏感访问审计页（参考 mcp-memory-service Dashboard 但更轻）
32. **Obsidian 兼容**——记忆目录可作为 Obsidian vault 打开，frontmatter / WikiLinks / Dataview 都可用；Markdown schema 与 Basic Memory 对齐（避免后续切 Obsidian 工具链时改格式）

## 5. 用户视角的典型场景（5 个）

### 场景 A：周一早上，CC 打开旧项目

`cd ~/projects/wolin` → `claude`。无任何弹窗。问"继续上周的 logo 方向"。Claude 内部静默调 `search_memory("logo wolin")`，回拿摘要后直接基于上下文响应，不需要用户重讲。

### 场景 B：周二下午，切到 Codex 跑同项目

同一目录开 Codex。问"测一下 logo 生成接口"。Codex 静默拿到 CC 周一写的项目摘要，直接懂上下文。

### 场景 C：周三在敏感作用域

`cd ~/scopes/finance`，问"上月发票尾款收到了吗"。屏幕弹授权框：

```
🔒 智能体请求读取 ~/scopes/finance 的记忆
  查询：上月沃林发票尾款
  授权范围？
  [1] 仅本次回答
  [2] 整个本会话
  [3] 本任务
  [4] 拒绝
```

用户选 2，本会话内 Claude 都能读，会话结束自动失效。`memory audit --scope=finance` 可查所有访问。

### 场景 D：周日早上 5 分钟复盘

桌面通知"memoryd weekly digest ready"。运行 `memory digest`，TUI 显示三栏（候选提升 / 重复合并 / TTL 到期），按 `a` 全批准或逐条审，5-10 分钟搞定。

### 场景 E：换电脑

新 Mac：从同步盘拉到 `memories.json` → `memory import mcp-memory-service ./memories.json`（如果是从老系统迁移）或 `memory restore`（如果是同系统多机）。30 分钟后所有记忆可用，敏感作用域需要在新机器单独标记并迁移密钥。

## 6. v1 明确不做的事（边界）

| 不做 | 理由 |
|---|---|
| ❌ 云端 SaaS / 多人共享同库 | 个人本地优先 |
| ❌ 会话开始自动注入"上次聊到什么" | 用户选默认静默 |
| ❌ 每次会话弹审核清单 | 用户选周期复盘 |
| ❌ 内容级敏感识别（密码 / 身份证扫描） | 用户选目录级声明 |
| ❌ 代码语义索引 / AST 切分 / VS Code 扩展（claude-context 集成） | 不在首要痛点；v2 视真实痛点重评 |
| ❌ Claude Desktop 自动捕获 | 不在三端范围 |
| ❌ 飞书 / 企微 / 邮件等渠道集成 | 不在用户当前画像；v2+ 作为 plugin |
| ❌ Web 上做记忆编辑（Web 仅浏览） | 编辑走 CLI / Markdown 直编 / Obsidian |
| ❌ 包装命令（`memcc` / `memcodex` 启动器） | 用户要保留原生体验 |
| ❌ 双向同步 CLAUDE.md / AGENTS.md / auto-memory | 避免冲突；只单向 import |

## 7. 开源资产复用决定

经过对 detailed-plan §4 五仓 + 4 月深调研 9 仓 + mcp-memory-service 三套资料的综合评估：

| 资产 | v1 怎么用 | 理由 |
|---|---|---|
| **memsearch** | **直接 fork 嵌入子模块** | 唯一已实现 CC/Codex/OpenClaw 三端钩子的开源仓；fork 后不受 upstream 拖累 |
| **engram** | **借鉴 SQLite schema、CLI 命令树、TUI 设计；不复用代码** | Go 栈与我们大概率不同；只取设计思路 |
| **mcp-memory-service** | **借鉴多电脑同步方案 + LaunchAgent/Task Scheduler 自启 + 5 阶段 consolidation 中的 decay + compression + soft-forgetting** | 这部分在 mcp-memory-service 最成熟；其他部分（HTTP MCP / Web Dashboard）是 v1 设计参考但不直接复用代码 |
| **方案 B（4 月深调研）** | **照搬 Tier 1/2/3 目录分层 + 4 准则过滤 prompt + memory-searcher sub-agent 设计 + frontmatter schema** | 4 月详细评估的成果，是真正的资产，不重新发明 |
| **Basic Memory** | **不引入代码，但 Markdown schema 与之对齐** | 用户选全自动捕获，Basic Memory 是显式 write_note 模型，不匹配；schema 兼容是为 v2 Obsidian 接入留路 |
| **claude-mem** | **借鉴 PreCompact prompt、status_line 状态展示、viewer 设计** | 单端方案，与三端定位重叠，但 Claude Code 端体验细节值得学 |
| **claude-context** | **不进 v1**，v2 视真实痛点重评 | 代码索引不在首要痛点；进 v1 让工程量翻倍 |
| **mem0** | **不复用** | 4 月研究已淘汰（栈重、外发 OpenAI、SaaS MCP），detailed-plan 里再列出来是 redundant |
| **feishu-user-plugin** | **不进 v1**，留作 v2 channel plugin 接口 | 不在用户当前多领域画像；team-skills 内部资产，不强求 v1 集成 |

## 8. 三端原生记忆机制处理

| 端 | 自带机制 | 我们的处理 |
|---|---|---|
| **Claude Code** | `CLAUDE.md`（多层级递归全量加载） | **不接管**。用户继续手写硬约束 / 铁律 |
| | `auto-memory MEMORY.md`（`~/.claude/projects/<proj>/memory/` 自动写入） | **不接管**；提供 `memory import auto-memory` 单向导入 |
| | `/memory` 命令、`#` 添加 | 保留原生使用 |
| | Skills、Sub-agents | 提供 `memory-searcher` sub-agent 模板（可选装） |
| **Codex** | `AGENTS.md` / `codex.md` | **不接管**；提供 `memory import agents-md` 单向导入 |
| **OpenClaw** | 内置 memory module | **不接管**；OpenClaw 插件配置里把 memoryd MCP 设为高优先工具，原生 module 仍可用作 fallback |

**绝对不做**：
- 替换或重写 CLAUDE.md / AGENTS.md / auto-memory 加载逻辑
- 在 CLAUDE.md 自动注入工作记忆（违反"默认静默"）
- memoryd ↔ auto-memory 双向同步（避免冲突）
- 强制用户用包装命令启动三端

## 9. 成功判据（3-6 个月后复盘用）

v1 上线后，下面任一条**未达成**即认为 v1 未真正交付：

1. **冷启动恢复**：某项目 ≥ 2 周不碰，再开任意端会话，智能体能在 < 1 分钟（含 1 次 memory MCP 调用）拿到工作上下文（不需要用户重讲）
2. **三端共享**：CC 写的工作记忆，Codex 和 OpenClaw 在同一目录的会话里能召回；反向同样
3. **召回精度**：智能体调用 memory MCP 时，召回相关性主观满意度 > 70%（每月人工抽样评估）
4. **复盘负担**：每周 5–10 分钟扫一次 digest 不会嫌烦；记忆库不会在 3 个月内变成"垃圾场"（高低价值比 > 60%）
5. **多端跨设备**：Mac 写的记忆，Windows / Linux 重启后能完整召回，敏感记忆需重新授权但不丢失
6. **token 预算**：三端常驻总 token < 25K（占 200K 窗口 < 12.5%）
7. **零原生破坏**：CLAUDE.md / AGENTS.md / OpenClaw 插件配置都没被我们的系统篡改
8. **敏感审计有效**：用户能在 60 秒内查到"过去 7 天哪个智能体读了 finance scope 多少次"

## 10. v2+ 议题（不进 v1）

下列议题在 v1 不做，但 spec 中明确路径，避免 v1 设计绕死：

1. **代码语义索引**：当用户某月反复抱怨"找不到那段代码"时启动；评估 claude-context 或自研轻量方案（避免向量库依赖）
2. **Obsidian 双向工作流**：v1 schema 已兼容；v2 装 Basic Memory MCP + Obsidian Local REST API + sync --watch
3. **飞书 / 企微 / 邮件 channel plugin**：v1 留 plugin 接口；v2 做第一个 channel plugin（feishu-user-plugin 复用）
4. **Claude Desktop 接入**：v2 视真实需求评估
5. **团队化 / 多人共享**：v3+，先解决个人闭环
6. **Association / Relationship Inference**（mcp-memory-service 5 阶段中的高阶）：v2 评估真实价值
7. **Web Dashboard 编辑能力**：v1 仅浏览，v2 视使用频率决定加不加编辑

## 11. 关于这份 spec 的演化

这份 spec 是 **冻结的用户契约**——一旦 v1 进入实施，不轻易修改。如果实施过程中发现某条立场不可达成或与现实冲突：

1. **先回到这份 spec 修条款**，写明修改原因
2. 再调整下游 sub-design
3. 不要在 sub-design 里偷偷绕过 spec

每次重大修改后，本节加一行变更记录：

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-05-09 | 初版 | brainstorm 完成 |
| 2026-05-15 | §4.5 #17 增补可选 passphrase-derived 模式 | Plan 6 多电脑同步 light brainstorming：用户选 passphrase 推导以使 `.md.enc` 跨设备可解。**原默认（per-scope random key 进本地 keychain）保留为 v1 default**；passphrase 模式为 opt-in，由 `[sensitive] key_source = "passphrase"` 启用，trade-off：以"用户须记住一个独立 master passphrase"换取"敏感记忆跨设备直接可用"。Plan 6 实施后本条不再视为冲突。 |

---

**附：与现有规划文档的关系**

- `docs/roadmap.md`：阶段路线（Phase 0-6）和阶段范围；本 spec 不重复
- `docs/detailed-plan.md`：技术架构、复用判断、SQLite/Markdown 决策；本 spec **覆盖**§2.5（记忆类型清单可参考但以本 spec §3 为准）和 §11（v1 边界以本 spec §6 为准）
- `记忆系统设计/05-方案B深度设计.md`：4 月针对营销负责人画像的方案；本 spec 复用其 Tier 分层、4 准则、sub-agent 设计、frontmatter schema，画像范围扩大到多领域个人
- `记忆库/README.md`：mcp-memory-service 安装包；本 spec 复用其多设备同步方案、LaunchAgent/TaskScheduler 自启、5 阶段 consolidation 中的可借鉴部分
