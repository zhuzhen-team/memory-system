# memory-system

> 完整文档：**https://EthanQC.github.io/memory-system/**

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)
![Docs](https://img.shields.io/badge/docs-MkDocs%20Material-526CFE)

本地优先的个人记忆系统。给 **Claude Code / Codex / OpenClaw** 三端 AI 提供同一份本地、可加密、可同步、会自动学习用户画像的记忆底座。

> **设计目标**：无论今天用哪一个 AI，明天换一个 AI，后天又换设备 —— 记忆都跟着人走，不跟着工具走。

## 5 分钟开始

**推荐：让 AI 替你装**。把下面整段（包括代码块）复制给你手头的 AI（CC / Codex / OpenClaw / 任意 MCP agent）：

```markdown
请帮我安装 memoryd（本地优先的个人记忆系统）。完整流程：

1. git clone https://github.com/EthanQC/memory-system 到 ~/memory-system
2. cd ~/memory-system/memoryd
3. uv venv && source .venv/bin/activate && uv pip install -e .
4. memoryd setup auto-install
   这一步会**真的一键装齐**：4 cron + CC SessionStart/End hooks + Codex notify
   wrapper（检测到 ~/.codex/config.toml 自动接） + launchd mirror（FS-watch 守护）
   + MCP 注册（写 ~/.claude.json 指 memoryd-mcp 13 工具） + LLM provider 默认
   切到 claude-code（如检测到本地 claude 命令）
5. memoryd doctor —— 一条命令验证全装：应该看到 overall=OK，17 项检查全 [OK]
   如果有 [WARN]，按提示跑对应子命令补
6. 可选：memoryd backfill --limit=50 —— 对装 LLM 之前已 capture 的历史 session
   批量补跑 KG 实体抽取 + DURA 评分，让画像立刻有料
7. 装好后重启 CC 让新 MCP 生效，`/mcp` 里应该看到 memoryd 下 13 个 mem_* 工具

不需要配 LLM API key —— capture / search / list / sync 全部本地工作。
"自动学习画像 / 实体抽取"等增强功能才用 LLM；auto-install 检测到本地 claude 命令
会自动把 provider 设成 claude-code，**复用 CC 订阅 quota，零额外费用**。
如果没装 claude CLI 且想要 LLM 功能：export ANTHROPIC_API_KEY=... 或装 Ollama。
```

手工安装路径见 [详细安装](https://EthanQC.github.io/memory-system/getting-started/installation/)。

## 装完后怎么验证它在工作

```bash
memoryd doctor              # ⭐ 一条命令告诉你系统在不在干活（17 项：binary/数据/hooks/codex/cron/MCP/LLM/...）
memoryd list --limit=10     # 现有记忆数
memoryd kg entities --top=20 # LLM 抽出的实体（人/项目/工具/概念...）
memoryd profile show        # 系统帮你写的画像 identity.md
memoryd web                 # 启动 Web Dashboard：浏览所有记忆 + 关系图 + identity 演化
                            # → 打开 http://127.0.0.1:8765（带 token 的 URL 会打到 stderr）
```

正常用 CC / Codex / OpenClaw 即可，会话结束会自动 capture。要查回历史时直接对 AI 说自然语言（"我之前怎么决定 X 的？" / "记一下这个决策：……"），AI 会自动调 `mem_search` / `mem_save` 等 MCP 工具。

## 它能做什么

- **三端打通**：CC 用原生 SessionStart/End hook，Codex 用 notify wrapper + 文件系统监听，OpenClaw 用原生 plugin。三端写同一记忆库。
- **本地优先**：所有记忆默认存在 `~/.local/share/memoryd/` 下的 Markdown + SQLite 索引。零云端依赖。
- **会自动学习**：每周 LLM 重写 `identity.md`；每月生成画像变化报告；新会话 SessionStart 注入画像 + top 实体 + 最近决策。
- **混合搜索**：ripgrep 关键词 + Milvus Lite 向量（bge-m3 ONNX 本地默认）+ RRF 重排 + 实体加权。
- **跨设备同步**：标准 `memories.json` 格式（兼容 mcp-memory-service v5），任意云盘同步；敏感记忆 AES-256-GCM 加密。
- **可审批**：会话摘要先入"工作记忆"，DURA 4 准则评分 + 用户审批通过后才升为"长期记忆"。

## LLM API key 要不要

| 你想做什么 | 需要 LLM key 吗 |
|---|---|
| 装 / 启动 / `capture` / `list` / `search` / `show` | **不要**。完全本地工作 |
| `sync export` / `sync import`（跨设备） | 不要 |
| `sensitive` 标敏感 + 加密 | 不要 |
| SessionStart `inject` 注入画像 | 不要（只读已有 `identity.md`） |
| 自动抽实体 + 写关系（KG） | **需要** |
| 每周重写 `identity.md`（画像自学习） | **需要** |
| 月度画像变化报告 | **需要** |
| `mem_judge` / `mem_compare`（supersedes 自动检测） | **需要** |

需要 LLM 时，4 条路径选一条（推荐第一条复用已有 CC 订阅）：

| provider | 怎么配 | 成本 |
|---|---|---|
| **`claude-code`** | 已经在用 CC 订阅，零额外配置（[复用机制详解](https://EthanQC.github.io/memory-system/user/reusing-claude-code/)） | $0（用 CC 订阅 quota） |
| `anthropic` | `export ANTHROPIC_API_KEY=sk-ant-...` | 按 token |
| `openai` | `export OPENAI_API_KEY=...` | 按 token |
| `ollama` | 本地装 ollama + 跑 model | $0（本地） |

详细每条命令"用不用 LLM、跳过会怎样"见 [LLM provider 选项](https://EthanQC.github.io/memory-system/user/llm-providers/)。

## 文档

完整站：**https://EthanQC.github.io/memory-system/**

### 给用户（你只想用 memoryd）

- [让 AI 帮你装](https://EthanQC.github.io/memory-system/user/install-via-ai/) · [5 分钟开始](https://EthanQC.github.io/memory-system/getting-started/quickstart/) · [核心概念](https://EthanQC.github.io/memory-system/getting-started/concepts/)
- [使用教程](https://EthanQC.github.io/memory-system/tutorials/)（9 篇实战）
- [三端集成](https://EthanQC.github.io/memory-system/integrations/claude-code/)（CC / Codex / OpenClaw）
- [日常运维](https://EthanQC.github.io/memory-system/operations/daily/) · [卸载](https://EthanQC.github.io/memory-system/operations/uninstall/)
- [LLM provider 选项](https://EthanQC.github.io/memory-system/user/llm-providers/) · [故障排查](https://EthanQC.github.io/memory-system/operations/troubleshooting/) · [FAQ](https://EthanQC.github.io/memory-system/faq/)

### 给开发者（你想读源码 / 改它 / 贡献）

- [架构全景](https://EthanQC.github.io/memory-system/architecture/overview/)（存储 / 搜索 / 治理 / KG / 画像 / 同步）
- 参考：[数据模型](https://EthanQC.github.io/memory-system/reference/data-model/) · [CLI](https://EthanQC.github.io/memory-system/reference/cli/) · [MCP 工具](https://EthanQC.github.io/memory-system/reference/mcp-tools/) · [Web 仪表板](https://EthanQC.github.io/memory-system/reference/web-dashboard/)
- [仓库结构](https://EthanQC.github.io/memory-system/development/repo-layout/) · [测试](https://EthanQC.github.io/memory-system/development/testing/) · [贡献](https://EthanQC.github.io/memory-system/development/contributing/)

## License

memory-system 自身代码：**MIT**。各模块按文件 fork 自上游：mem0 (Apache-2.0) / claude-mem (MIT) / memsearch (MIT) / engram (MIT) / claude-context (MIT)。fork 文件头标注上游 path + license。
