# memory-system

> 📚 文档：**https://EthanQC.github.io/memory-system/**

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)
![Docs](https://img.shields.io/badge/docs-MkDocs%20Material-526CFE)

本地优先的个人记忆系统。装一次，**Claude Code / Codex / OpenClaw 永远记得你**——你的项目、决策、技术栈、协作风格。换 AI / 换电脑都不丢。

> 设计目标：**无论今天用哪个 AI、明天换一个、后天换设备 —— 记忆跟着你，不跟着工具**。

## 它解决什么具体问题

你应该熟悉这些场景：

- 给 AI 解释了三遍"我项目叫 X，技术栈是 Y，偏好 Z" —— 换个会话又得从头讲
- CC 里聊好的方案，切到 Codex 它一无所知
- 换电脑要把所有 prompt / 偏好 / `CLAUDE.md` 重写
- 一个月前的决策为什么这么定，再也想不起来
- 给同事看你的 AI 工作流 —— 没法演示"AI 真的懂我"

memoryd 就是为这些痛点做的。装好之后，**你跟 AI 正常聊天，它在后台用 LLM 自动学你**——抽实体、写关系图、每周重写一份你的画像 `identity.md`、每月生成变化报告。三端 AI 都读同一份画像。

## 3 步装（其中 2 步让 AI 替你跑）

**前置**：你已经在用 Claude Code 且已登录（CC 用户都有）。

### 第 1 步：复制下面这段 prompt 给 CC

```markdown
请帮我安装 memoryd（本地优先的个人记忆系统）。完整流程：

1. git clone https://github.com/EthanQC/memory-system 到 ~/memory-system
2. cd ~/memory-system/memoryd
3. uv venv && source .venv/bin/activate && uv pip install -e .
4. memoryd setup auto-install
   会一键装齐：4 cron + CC SessionStart/End hooks + Codex notify wrapper（自动检测） + 
   launchd FS-watch 守护 + MCP 注册到 ~/.claude.json（16 个 mem_* 工具）+ 
   LLM provider 自动设成 claude-code（用我的 CC 订阅，零额外费）+ 写
   ~/.codex/AGENTS.md 让 Codex 也读到我的画像。
5. memoryd doctor 验证：overall=OK 即装好。

完全不需要配 LLM API key —— 整套用我已经在用的 Claude Code 订阅跑。
跑完告诉我可以重启 CC 了。
```

AI 会自动跑完 80 秒左右。

### 第 2 步：重启 Claude Code

让新装的 MCP server 重新连接（这样 CC 才能调 `mem_*` 16 个工具）。

### 第 3 步：验证 AI 真认识你

重启后，跟 CC 说一句：

> **"你认识我吗？我最近在做什么项目？"**

如果 CC 答案里出现你的真实项目名、技术偏好、最近决策 —— **整套工作起来了**。AI 看到的就是后台自动学出来的画像。

（你在 CC UI 里**不会显式看到画像内容**，因为它走 `additionalContext` 通道注入给模型层；AI 读得到，你不用读。）

## 装完后系统每天替你做的（你不用碰）

| 时机 | 自动发生 |
|---|---|
| 每次会话结束 | capture session → LLM 抽实体 + 写关系 + DURA 评分 → 高分（≥0.85）直接进 long-term，灰区留 pending |
| 每次会话开场 | SessionStart hook 把你的画像 + top 实体 + 最近决策 + 待审批提醒注入给 AI |
| 每天 03:00 | decay 扫描（90 天无召回的记忆降级） |
| 每周一 09:00 | digest 弹 macOS 通知，回顾 pending / 即将 decay / trends |
| 每周日 02:00 | LLM 重写 `identity.md`（本周新内容 + 高召回实体融合） |
| 每月 1 日 04:00 | 月度变化报告（"4 月你偏好 React，5 月切到 Solid"这种） |

## 你能主动用的 5 件事（都是一句话）

| 想做啥 | 怎么做 |
|---|---|
| **问 AI 历史** | 在 CC 里说自然语言："我之前怎么决定 X 的？" / "上次说的那个 bug 修了吗？" |
| **批 pending** | 在 CC 里说"过一遍 pending"，CC 调 `mem_review_pending` 列出来帮你判断 |
| **看自己画像** | 终端 `memoryd profile show` 或 `memoryd web` 浏览器看 |
| **跨设备同步** | `memoryd config set sync.dir ~/Dropbox/memoryd` + `memoryd setup auto-install` —— 配一次，cron 每天 03:30 自动 push |
| **整包备份 / 迁移** | `memoryd sync bundle` —— 一条命令，桌面拿 tar.gz（含全部 markdown + identity + 索引 + 审计链） |

## LLM 配置：完全不用配 key

memoryd 自动用你 Claude Code 订阅的 quota 跑所有 LLM 功能。原理：内部 spawn `claude -p` 一次性调用，**走你 CC 已登录账号 = 零 API key**。

```bash
memoryd doctor   # 看到 [OK] LLM provider: claude-code 就是正常
memoryd llm test # 实测 8 秒返回 'OK' = 通路工作
```

不想用 CC 订阅？还有 3 个选项：

| provider | 怎么切 |
|---|---|
| `anthropic` | `export ANTHROPIC_API_KEY=...` + `memoryd config set llm.provider anthropic` |
| `openai` | `export OPENAI_API_KEY=...` + `memoryd config set llm.provider openai` |
| `ollama` | 完全本地，`ollama serve` + `memoryd config set llm.provider ollama` |

详细：[复用 Claude Code 详解](https://EthanQC.github.io/memory-system/user/reusing-claude-code/)

## 仓库结构

```
memory-system/
├── memoryd/              # Python daemon —— 核心引擎
│   └── src/memoryd/      # capture / search / governance / KG / profile / sync / mcp_tools
├── plugins/              # 三端胶水
│   ├── claude-code/      # SessionStart 注入 + SessionEnd capture（sh/py/ps1）
│   ├── codex/            # notify wrapper + AGENTS.md 自动注入
│   └── openclaw/         # native plugin（Node ≥18）
├── docs/                 # MkDocs Material 站点
└── .github/workflows/    # docs 自动部署
```

## 文档入口

完整站：**https://EthanQC.github.io/memory-system/**

- **入门**：[让 AI 帮你装](https://EthanQC.github.io/memory-system/user/install-via-ai/) · [5 分钟上手](https://EthanQC.github.io/memory-system/getting-started/quickstart/) · [核心概念](https://EthanQC.github.io/memory-system/getting-started/concepts/) · [手工安装](https://EthanQC.github.io/memory-system/getting-started/installation/)
- **教程**：[第一次记忆](https://EthanQC.github.io/memory-system/tutorials/first-memory/) · [自动捕获](https://EthanQC.github.io/memory-system/tutorials/auto-capture/) · [搜索召回](https://EthanQC.github.io/memory-system/tutorials/search-and-recall/) · [知识图谱](https://EthanQC.github.io/memory-system/tutorials/knowledge-graph/) · [画像自学习](https://EthanQC.github.io/memory-system/tutorials/profile-self-learning/) · [跨设备同步](https://EthanQC.github.io/memory-system/tutorials/cross-device-sync/) · [敏感记忆](https://EthanQC.github.io/memory-system/tutorials/sensitive-memories/)
- **集成**：[Claude Code](https://EthanQC.github.io/memory-system/integrations/claude-code/) · [Codex](https://EthanQC.github.io/memory-system/integrations/codex/) · [OpenClaw](https://EthanQC.github.io/memory-system/integrations/openclaw/)
- **配置**：[复用 Claude Code](https://EthanQC.github.io/memory-system/user/reusing-claude-code/) · [其他 LLM provider](https://EthanQC.github.io/memory-system/user/llm-providers/) · [跨设备同步](https://EthanQC.github.io/memory-system/operations/sync-setup/) · [加密](https://EthanQC.github.io/memory-system/operations/encryption/)
- **运维**：[日常使用](https://EthanQC.github.io/memory-system/operations/daily/) · [定时任务](https://EthanQC.github.io/memory-system/operations/cron/) · [健康检查](https://EthanQC.github.io/memory-system/operations/health-check/) · [故障排查](https://EthanQC.github.io/memory-system/operations/troubleshooting/) · [卸载](https://EthanQC.github.io/memory-system/operations/uninstall/)
- **架构**：[架构全景](https://EthanQC.github.io/memory-system/architecture/overview/) · [存储](https://EthanQC.github.io/memory-system/architecture/storage/) · [搜索](https://EthanQC.github.io/memory-system/architecture/search/) · [治理](https://EthanQC.github.io/memory-system/architecture/governance/) · [知识图谱](https://EthanQC.github.io/memory-system/architecture/knowledge-graph/) · [画像自学习](https://EthanQC.github.io/memory-system/architecture/profile-learning/) · [跨设备同步](https://EthanQC.github.io/memory-system/architecture/sync/)
- **参考**：[数据模型](https://EthanQC.github.io/memory-system/reference/data-model/) · [CLI 命令](https://EthanQC.github.io/memory-system/reference/cli/) · [MCP 工具](https://EthanQC.github.io/memory-system/reference/mcp-tools/) · [memories.json 格式](https://EthanQC.github.io/memory-system/reference/memories-json/) · [Web 仪表板](https://EthanQC.github.io/memory-system/reference/web-dashboard/) · [仓库结构](https://EthanQC.github.io/memory-system/development/repo-layout/) · [测试](https://EthanQC.github.io/memory-system/development/testing/) · [贡献](https://EthanQC.github.io/memory-system/development/contributing/)
- **常见问题**：[FAQ](https://EthanQC.github.io/memory-system/faq/)

## 适用范围

memoryd 是给 Claude Code / Codex / OpenClaw 三端 AI 准备的本地记忆底座，需要本机能挂 hook / cron / MCP（macOS、Linux 已支持，Windows 在路上）。所有数据存在本地 `~/.local/share/memoryd/`，LLM 调用走你已有的 Claude Code 订阅或自配 API key。
