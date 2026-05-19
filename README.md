# memory-system

> 完整文档：**https://zhuzhen-team.github.io/memory-system/**

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)
![Tests](https://img.shields.io/badge/tests-370%2B%20passing-brightgreen)
![Docs](https://img.shields.io/badge/docs-MkDocs%20Material-526CFE)

本地优先的个人记忆系统。给 **Claude Code / Codex / OpenClaw** 三端 AI 提供同一份本地、可加密、可同步、会自动学习用户画像的记忆底座。

> **设计目标**：无论今天用哪一个 AI，明天换一个 AI，后天又换设备 —— 记忆都跟着人走，不跟着工具走。

## 它能做什么

- **三端打通**：CC 用原生 hook，Codex 用 notify wrapper + 文件系统监听，OpenClaw 用原生 TS plugin。三端写入同一记忆库，互相读得到对方写的内容。
- **本地优先**：所有记忆默认存在本机 `~/.local/share/memoryd/` 下的 Markdown 文件 + SQLite 索引。
- **会自动学习**：每次会话结束自动抽实体、写关系、检测决策演化；每周 LLM 重写 `identity.md`；每月生成画像变化报告。
- **混合搜索**：ripgrep 关键词 + Milvus Lite 向量（bge-m3 ONNX 本地默认）+ RRF 重排 + 实体加权。
- **跨设备同步**：标准 `memories.json` 格式（兼容 mcp-memory-service v5），可经任意云盘同步。敏感记忆本地加密、跨机用 passphrase。
- **可审批**：会话摘要先入"工作记忆"，DURA 4 准则评分 + 用户审批通过后才升为"长期记忆"。
- **敏感保护**：标记敏感的 scope 自动 AES-256-GCM 加密 + 授权访问 + 审计链。

## 仓库结构

```
memory-system/
├── memoryd/              # Python daemon：核心引擎
│   └── src/memoryd/
│       ├── cli.py / mcp_server.py / schema.py / storage.py / ...
│       ├── governance/   # DURA / decay / digest / audit
│       ├── search/       # vector / hybrid / scoring / sessions
│       ├── embeddings/   # ONNX bge-m3 + OpenAI
│       ├── llm/          # anthropic / openai / ollama + prompts
│       ├── knowledge_graph/  # entities + relations + supersedes
│       ├── profile/      # weekly identity + 月度报告 + trends
│       ├── sync/         # 路径 A markdown + 路径 B memories.json
│       ├── mcp_tools/    # 19 个 mem_* 工具
│       └── ...
├── plugins/              # 三端胶水
│   ├── claude-code/      # SessionEnd hook 跨平台脚本
│   ├── codex/            # notify wrapper + launchd plist
│   └── openclaw/         # native plugin（TS / Node ≥18）
├── docs/                 # MkDocs 文档源（实时同步代码状态）
├── vendor/               # 外部仓副本（研究用 + 按文件 fork）
├── mkdocs.yml
└── .github/workflows/    # docs 自动部署
```

## 快速开始

```bash
git clone https://github.com/zhuzhen-team/memory-system ~/memory-system
cd ~/memory-system/memoryd

uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 一键挂三端 + 跨平台 cron
memoryd setup auto-install

# 配 LLM（强烈推荐）
export ANTHROPIC_API_KEY=sk-ant-xxx
memoryd config set llm.provider anthropic
memoryd config set llm.model claude-haiku-4-5

# 跑测试
uv run pytest -v
```

详细步骤见 [文档站 · 安装](https://zhuzhen-team.github.io/memory-system/getting-started/installation/)。

## 当前状态

memoryd v1.0.0 — 全部核心模块已落地，v1 spec §4 列的 32 项功能全部交付：

- 三端 capture（CC SessionEnd hook + Codex 双通路 + OpenClaw native plugin）
- Markdown SoT + SQLite index + 加密 `.md.enc`
- DURA 治理 + decay + digest + TUI 审批
- 知识图谱（entities / relations / supersedes_chain）
- 画像自学习（weekly identity / 月度变化报告 / trends）
- 混合搜索（ripgrep × Milvus Lite RRF + 实体加权）
- 跨设备同步（路径 A 增量 markdown + 路径 B memories.json）
- 19 个 `mem_*` MCP 工具（13 agent + 6 admin）
- Web Dashboard 11 个路由 + 4 个新页面（首页 / relations / trends / identity）
- 跨平台（macOS / Linux / Windows）

约 370+ 测试，全通过。

## 文档入口

完整文档：https://zhuzhen-team.github.io/memory-system/

主要页面：

- [项目概览](https://zhuzhen-team.github.io/memory-system/getting-started/overview/)
- [安装](https://zhuzhen-team.github.io/memory-system/getting-started/installation/) · [首次运行](https://zhuzhen-team.github.io/memory-system/getting-started/first-run/) · [核心概念](https://zhuzhen-team.github.io/memory-system/getting-started/concepts/)
- [架构全景](https://zhuzhen-team.github.io/memory-system/architecture/overview/) · [存储层](https://zhuzhen-team.github.io/memory-system/architecture/storage/) · [搜索](https://zhuzhen-team.github.io/memory-system/architecture/search/) · [治理](https://zhuzhen-team.github.io/memory-system/architecture/governance/) · [知识图谱](https://zhuzhen-team.github.io/memory-system/architecture/knowledge-graph/) · [画像自学习](https://zhuzhen-team.github.io/memory-system/architecture/profile-learning/) · [跨设备同步](https://zhuzhen-team.github.io/memory-system/architecture/sync/)
- 三端集成：[Claude Code](https://zhuzhen-team.github.io/memory-system/integrations/claude-code/) · [Codex](https://zhuzhen-team.github.io/memory-system/integrations/codex/) · [OpenClaw](https://zhuzhen-team.github.io/memory-system/integrations/openclaw/)
- 参考：[数据模型](https://zhuzhen-team.github.io/memory-system/reference/data-model/) · [CLI](https://zhuzhen-team.github.io/memory-system/reference/cli/) · [MCP 工具](https://zhuzhen-team.github.io/memory-system/reference/mcp-tools/) · [memories.json 格式](https://zhuzhen-team.github.io/memory-system/reference/memories-json/)
- 运维：[日常使用](https://zhuzhen-team.github.io/memory-system/operations/daily/) · [定时任务](https://zhuzhen-team.github.io/memory-system/operations/cron/) · [同步配置](https://zhuzhen-team.github.io/memory-system/operations/sync-setup/) · [加密](https://zhuzhen-team.github.io/memory-system/operations/encryption/) · [故障排查](https://zhuzhen-team.github.io/memory-system/operations/troubleshooting/)
- 开发：[仓库结构](https://zhuzhen-team.github.io/memory-system/development/repo-layout/) · [模块来源](https://zhuzhen-team.github.io/memory-system/development/module-fork-map/) · [测试](https://zhuzhen-team.github.io/memory-system/development/testing/) · [贡献](https://zhuzhen-team.github.io/memory-system/development/contributing/)

## License

memory-system 自身代码：**MIT**。

`vendor/` 子目录是上游仓库副本，各自携带原 license。fork 进 memoryd 的具体文件见
[模块来源](https://zhuzhen-team.github.io/memory-system/development/module-fork-map/)。
