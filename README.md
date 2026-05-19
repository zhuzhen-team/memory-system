# memory-system

> 📚 完整文档：**https://zhuzhen-team.github.io/memory-system/**
> 🚀 5 分钟上手：[快速开始](https://zhuzhen-team.github.io/memory-system/getting-started/quickstart/)

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)
![Tests](https://img.shields.io/badge/tests-716%20passing-brightgreen)
![Docs](https://img.shields.io/badge/docs-MkDocs%20Material-526CFE)
![MCP](https://img.shields.io/badge/MCP-19%20tools-purple)

本地优先的个人记忆系统。给 **Claude Code / Codex / OpenClaw** 三端 AI 提供同一份本地、可加密、可同步、会自动学习用户画像的记忆底座。

> **设计目标**：无论今天用哪一个 AI，明天换一个 AI，后天又换设备 —— 记忆都跟着人走，不跟着工具走。

## 它能做什么

- **三端打通**：CC 用原生 SessionStart/End hook，Codex 用 notify wrapper + 文件系统监听，OpenClaw 用原生 plugin（3 工具 + 2 hook）。三端写入同一记忆库，互相读得到对方写的内容。
- **本地优先**：所有记忆默认存在本机 `~/.local/share/memoryd/` 下的 Markdown 文件 + SQLite 索引。零云端依赖。
- **会自动学习**：每次会话结束自动抽实体、写关系、检测决策演化；每周 LLM 重写 `identity.md`；每月生成画像变化报告。新会话开场时 SessionStart hook 把画像 + top 实体 + 最近决策注入给 AI，AI 一开始就"认识"你。
- **混合搜索**：ripgrep 关键词 + Milvus Lite 向量（bge-m3 ONNX 本地默认）+ RRF 重排 + 实体加权。
- **跨设备同步**：标准 `memories.json` 格式（向后兼容 mcp-memory-service v5），可经任意云盘同步（iCloud / Dropbox / Syncthing / git 都行）。敏感记忆本地 AES-256-GCM 加密、跨机用 passphrase。
- **可审批**：会话摘要先入"工作记忆"，DURA 4 准则评分 + 用户审批通过后才升为"长期记忆"——避免 AI 自己说的垃圾喂坏画像。
- **敏感保护**：标记敏感的 scope 自动加密 + 授权访问 + SHA256 审计链。

## 5 分钟跑起来

```bash
git clone https://github.com/zhuzhen-team/memory-system ~/memory-system
cd ~/memory-system/memoryd

uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 一键挂三端 hook + 跨平台 cron + 后台守护
memoryd setup auto-install

# 配 LLM（强烈推荐，免费的 Ollama 也行）
export ANTHROPIC_API_KEY=sk-ant-xxx
memoryd config set llm.provider anthropic
memoryd config set llm.model claude-haiku-4-5

# 第一次手动 capture 一条记忆，验证安装
echo "我决定用 memoryd 作为主力记忆系统" | memoryd capture --source=manual
memoryd list --limit=5
```

接下来你正常用 CC / Codex / OpenClaw 即可，会话结束时会自动 capture。需要查回历史时直接问 AI："我之前怎么决定 X 的？" CC 会自动调 `mem_search` 工具。

详细安装步骤见 [文档站 · 5 分钟快速开始](https://zhuzhen-team.github.io/memory-system/getting-started/quickstart/)，遇到问题看 [FAQ](https://zhuzhen-team.github.io/memory-system/faq/) 或 [故障排查](https://zhuzhen-team.github.io/memory-system/operations/troubleshooting/)。

## 仓库结构

```
memory-system/
├── memoryd/              # Python daemon：核心引擎（87 个 .py 文件）
│   └── src/memoryd/
│       ├── cli.py / mcp_server.py / inject.py / schema.py / storage.py / ...
│       ├── governance/   # DURA / decay / digest / audit
│       ├── search/       # vector (Milvus Lite) / hybrid / scoring / sessions
│       ├── embeddings/   # ONNX bge-m3（本地默认） + OpenAI
│       ├── llm/          # anthropic / openai / ollama + prompts
│       ├── knowledge_graph/  # entities + relations + supersedes 自动检测
│       ├── profile/      # weekly identity 重写 + 月度报告 + trends
│       ├── sync/         # 路径 A markdown 增量 + 路径 B memories.json v5
│       ├── mcp_tools/    # 19 个 mem_* 工具（13 agent + 6 admin）
│       ├── web/          # FastAPI Dashboard（11 路由 + 4 页面）
│       └── ...
├── plugins/              # 三端胶水（用户安装时引用绝对路径）
│   ├── claude-code/      # SessionStart 注入 + SessionEnd capture（sh/py/ps1）
│   ├── codex/            # notify wrapper + launchd plist
│   └── openclaw/         # native plugin（Node ≥18 stdlib，零 npm install）
├── docs/                 # MkDocs Material 源（45+ 文档，含教程系列 + FAQ）
├── mkdocs.yml
└── .github/workflows/    # docs.yml 自动部署到 GitHub Pages
```

## 当前状态

memoryd v1.0.0 — 全部核心模块已落地：

| 维度 | 实现 |
|---|---|
| 三端 capture | ✅ CC SessionStart 注入 + SessionEnd capture + Codex 双通路 + OpenClaw native plugin |
| 存储 | ✅ Markdown SoT + SQLite + `.md.enc` 加密 |
| 治理 | ✅ DURA + decay + digest + TUI 审批 + audit chain |
| 知识图谱 | ✅ entities / relations / supersedes_chain + 自动检测 |
| 画像自学习 | ✅ weekly identity / 月度变化报告 / trigger 频次 trends |
| 混合搜索 | ✅ ripgrep × Milvus Lite × bge-m3 ONNX × RRF |
| 跨设备同步 | ✅ 路径 A 增量 markdown + 路径 B memories.json v5.1（向后兼容 v5.0） |
| MCP server | ✅ 19 个 `mem_*` 工具，stdio + http 双 transport |
| Web Dashboard | ✅ 11 路由（首页 + 关系图 + trends + identity 演化） |
| 跨平台 | ✅ macOS launchd / Linux systemd / Windows Task Scheduler |
| 定时任务 | ✅ decay / digest / weekly_identity / monthly_report |
| 测试 | ✅ 716 个测试，全部通过（含 1 个端到端 e2e） |

## 文档入口

完整文档站：https://zhuzhen-team.github.io/memory-system/

按角色看：

- **新用户**：[项目概览](https://zhuzhen-team.github.io/memory-system/getting-started/overview/) → [5 分钟快速开始](https://zhuzhen-team.github.io/memory-system/getting-started/quickstart/) → [详细安装](https://zhuzhen-team.github.io/memory-system/getting-started/installation/) → [首次运行](https://zhuzhen-team.github.io/memory-system/getting-started/first-run/)
- **想学透怎么用**：[教程系列](https://zhuzhen-team.github.io/memory-system/tutorials/) —— 9 篇实战教程，每篇 200-500 字带可复制命令
  - [第一次记忆](https://zhuzhen-team.github.io/memory-system/tutorials/first-memory/) · [自动捕获](https://zhuzhen-team.github.io/memory-system/tutorials/auto-capture/) · [搜索与召回](https://zhuzhen-team.github.io/memory-system/tutorials/search-and-recall/)
  - [知识图谱](https://zhuzhen-team.github.io/memory-system/tutorials/knowledge-graph/) · [画像自学习](https://zhuzhen-team.github.io/memory-system/tutorials/profile-self-learning/) · [跨设备同步](https://zhuzhen-team.github.io/memory-system/tutorials/cross-device-sync/) · [敏感记忆](https://zhuzhen-team.github.io/memory-system/tutorials/sensitive-memories/) · [故障诊断流](https://zhuzhen-team.github.io/memory-system/tutorials/troubleshooting-flow/)
- **常见问题**：[FAQ](https://zhuzhen-team.github.io/memory-system/faq/)（22 个真实用户问题）
- **想搞清架构**：[架构全景](https://zhuzhen-team.github.io/memory-system/architecture/overview/) · [存储层](https://zhuzhen-team.github.io/memory-system/architecture/storage/) · [搜索](https://zhuzhen-team.github.io/memory-system/architecture/search/) · [治理](https://zhuzhen-team.github.io/memory-system/architecture/governance/) · [知识图谱](https://zhuzhen-team.github.io/memory-system/architecture/knowledge-graph/) · [画像自学习](https://zhuzhen-team.github.io/memory-system/architecture/profile-learning/) · [跨设备同步](https://zhuzhen-team.github.io/memory-system/architecture/sync/)
- **三端接入细节**：[Claude Code](https://zhuzhen-team.github.io/memory-system/integrations/claude-code/) · [Codex](https://zhuzhen-team.github.io/memory-system/integrations/codex/) · [OpenClaw](https://zhuzhen-team.github.io/memory-system/integrations/openclaw/)
- **API / 接口手册**：[数据模型](https://zhuzhen-team.github.io/memory-system/reference/data-model/) · [CLI 命令](https://zhuzhen-team.github.io/memory-system/reference/cli/) · [MCP 工具](https://zhuzhen-team.github.io/memory-system/reference/mcp-tools/) · [memories.json 格式](https://zhuzhen-team.github.io/memory-system/reference/memories-json/) · [Web 仪表板](https://zhuzhen-team.github.io/memory-system/reference/web-dashboard/)
- **运维 + 卸载**：[日常使用](https://zhuzhen-team.github.io/memory-system/operations/daily/) · [定时任务](https://zhuzhen-team.github.io/memory-system/operations/cron/) · [同步配置](https://zhuzhen-team.github.io/memory-system/operations/sync-setup/) · [加密](https://zhuzhen-team.github.io/memory-system/operations/encryption/) · [故障排查](https://zhuzhen-team.github.io/memory-system/operations/troubleshooting/) · [卸载](https://zhuzhen-team.github.io/memory-system/operations/uninstall/)
- **想给项目贡献**：[仓库结构](https://zhuzhen-team.github.io/memory-system/development/repo-layout/) · [模块来源](https://zhuzhen-team.github.io/memory-system/development/module-fork-map/) · [测试](https://zhuzhen-team.github.io/memory-system/development/testing/) · [贡献指南](https://zhuzhen-team.github.io/memory-system/development/contributing/)

## License

memory-system 自身代码：**MIT**。

各模块按文件 fork 自上游（memsearch MIT / mem0 Apache-2.0 / claude-mem Apache-2.0 / engram MIT / claude-context MIT），逐文件追溯见 [模块来源](https://zhuzhen-team.github.io/memory-system/development/module-fork-map/)。
