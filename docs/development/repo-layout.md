---
title: 仓库结构
keywords: 仓库, 结构, 源码导览, memoryd, plugins, vendor
---

# 仓库结构：源码导览

## 顶层

```
memory-system/
├── memoryd/              # Python daemon：核心引擎
├── plugins/              # 三端胶水：claude-code / codex / openclaw
├── docs/                 # 文档站点（你正在看的）
├── vendor/               # 外部仓的本地副本（研究用 + 必要时按文件 fork）
├── mkdocs.yml            # MkDocs Material 配置
├── README.md
└── .github/workflows/    # CI：docs 部署
```

## memoryd/

```
memoryd/
├── pyproject.toml        # Python 包定义（依赖 + scripts）
├── README.md             # 详细使用 + plan 历史
├── src/memoryd/
│   ├── __init__.py
│   ├── cli.py            # 所有 CLI 子命令（一棵 argparse 树）
│   ├── server.py         # 旧 MCP server（search_memory 单工具）
│   ├── mcp_server.py     # 新 MCP server（19 mem_* 工具）
│   ├── schema.py         # frontmatter Pydantic schema
│   ├── storage.py        # Markdown 读写 + atomic write
│   ├── scope.py          # cwd → scope_hash
│   ├── scope_meta.py     # scope 元数据 (.scope-name 等)
│   ├── index.py          # SQLite 索引 + migrations runner
│   ├── enc.py            # AES-256-GCM
│   ├── passphrase.py     # PBKDF2 跨机派生
│   ├── chunking.py       # 标题切块 + SHA-256 去重
│   ├── config.py         # ~/.config/memoryd/config.toml 加载
│   ├── notify.py         # 跨平台 GUI 通知 + SMTP
│   ├── setup.py          # 用户配置管理子命令（notify swap / launchd 等）
│   ├── setup_cron.py     # cron 跨平台安装
│   ├── mirror.py         # FS-watch 路由框架（_unscoped 兜底）
│   ├── mirror_codex.py   # Codex rollout_summary 转码
│   ├── mirror_openclaw.py# OpenClaw session jsonl 转码
│   ├── governance/
│   │   ├── analyze.py    # DURA 评分
│   │   ├── decay.py      # 衰减状态机
│   │   ├── digest.py     # 周复盘
│   │   ├── merge.py      # 合并重复
│   │   ├── gate.py       # 敏感 scope 授权检查
│   │   ├── grants.py     # grant / revoke / 过期
│   │   └── audit.py      # 审计链
│   ├── embeddings/
│   │   ├── __init__.py   # Embedder Protocol + factory
│   │   ├── onnx_bge_m3.py # 默认：本地 bge-m3 ONNX
│   │   └── openai_provider.py
│   ├── search/
│   │   ├── __init__.py
│   │   ├── vector.py     # Milvus Lite wrapper
│   │   ├── hybrid.py     # RRF 重排 + 实体加权
│   │   ├── scoring.py    # BM25 归一化 + lemmatize
│   │   └── sessions.py   # ripgrep 关键词层
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py       # LLMProvider Protocol + LLMMessage / JudgeResult
│   │   ├── factory.py    # get_llm()
│   │   ├── anthropic_provider.py
│   │   ├── openai_provider.py
│   │   ├── ollama_provider.py
│   │   └── prompts/
│   │       ├── extract_entities.py
│   │       ├── judge_supersedes.py
│   │       ├── rewrite_identity.py
│   │       └── profile_change_report.py
│   ├── knowledge_graph/
│   │   ├── __init__.py
│   │   ├── store.py      # SQLite DAO (entities / relations / supersedes_chain)
│   │   ├── extract.py    # LLM-first + jieba 兜底
│   │   ├── relations.py  # ingest_extract_result + normalize_predicate
│   │   ├── supersedes.py # detect_supersedes_for_new_memory
│   │   ├── query.py      # memories_about_entity / n_hop_subgraph / ...
│   │   └── migrations.py # KG schema 自动建表
│   ├── profile/
│   │   ├── __init__.py
│   │   ├── store.py      # ProfileStore / ProfileVersion
│   │   ├── identity.py   # rewrite_identity_weekly
│   │   ├── trends.py     # trigger 频次 + 渲染
│   │   ├── evolution.py  # generate_monthly_change_report
│   │   └── migrations.py
│   ├── sync/
│   │   ├── __init__.py   # 路径 A 增量 markdown + 路径 B 重导出
│   │   ├── memories_json.py  # 路径 B 主逻辑
│   │   ├── schema.py     # 数据类
│   │   └── conflict.py   # 字段级合并
│   ├── mcp_tools/        # 19 个 mem_* 工具的处理函数
│   │   ├── __init__.py
│   │   ├── memory.py     # save / update / delete / get / search / context / timeline
│   │   ├── session.py    # session_start / session_end / session_summary / capture_passive
│   │   ├── judge.py      # judge / compare
│   │   ├── admin.py      # stats / merge_projects / current_project / doctor / save_prompt / suggest_topic_key
│   │   └── util.py       # 共享 helpers
│   ├── importers/        # 一次性 import
│   │   ├── claude_md.py
│   │   ├── auto_memory.py
│   │   ├── agents_md.py
│   │   └── mcp_memory_service.py
│   ├── tui/
│   │   └── digest.py     # textual digest 审批 TUI
│   ├── web/
│   │   ├── server.py     # FastAPI 启动
│   │   ├── routes.py     # 11 个路由
│   │   ├── templates/    # Jinja2
│   │   └── static/
│   ├── platforms/        # 跨平台 cron + launchd / systemd / Task Scheduler
│   │   ├── darwin.py
│   │   ├── linux.py
│   │   └── windows.py
│   ├── prompts/
│   │   └── dura_extract.txt   # Plan 3 遗留的 DURA prompt
│   ├── migrations/       # SQL 文件
│   │   ├── 001_initial_schema.sql
│   │   ├── 002_sensitive_scope.sql
│   │   ├── 003_sensitive_scopes_table.sql
│   │   ├── 004_knowledge_graph.sql
│   │   └── 005_profile_self_learning.sql
│   └── templates/        # memory-searcher.md 等 sub-agent 模板
└── tests/                # pytest，覆盖率 ~370 测试
```

## plugins/

```
plugins/
├── claude-code/
│   ├── session-end.sh    # macOS / Linux
│   ├── session-end.ps1   # Windows
│   └── session-end.py    # 通用 Python fallback
├── codex/
│   ├── notify-wrapper.sh # 实时通路
│   ├── notify-probe.sh   # 一次性诊断
│   └── launchd/
│       └── com.memoryd.mirror.plist
├── openclaw/             # OpenClaw native plugin (Node ≥ 18)
│   ├── openclaw.plugin.json
│   ├── package.json
│   ├── README.md
│   ├── src/
│   │   ├── index.js
│   │   ├── register.js   # 旧 SDK lifecycle 桥接
│   │   ├── memoryd_client.js
│   │   ├── payload.js
│   │   ├── hooks/
│   │   │   ├── before_agent_start.js
│   │   │   └── agent_end.js
│   │   └── tools/
│   │       ├── memory_search.js
│   │       ├── memory_get.js
│   │       └── memory_transcript.js
│   └── tests/
└── migrate-rename-to-memory-system.sh   # 旧名 project-management-personal → memory-system 的迁移脚本
```

## docs/

```
docs/
├── index.md
├── getting-started/      # overview / installation / first-run / concepts
├── architecture/         # overview / storage / search / governance / KG / profile / sync
├── integrations/         # claude-code / codex / openclaw
├── reference/            # data-model / cli / mcp-tools / web-dashboard / memories-json
├── operations/           # daily / cron / sync-setup / encryption / troubleshooting / uninstall
├── development/          # repo-layout / module-fork-map / testing / contributing
└── assets/               # 图片 / 截图
```

## vendor/

5 个外部仓的本地副本（研究用 + 必要时按文件 fork）。**不参与运行时**。

| 子目录 | 上游 | 我们借鉴 / fork 了什么 |
|---|---|---|
| `vendor/mem0` | mem0ai/mem0 | scoring 思路（BM25 + entity boost） |
| `vendor/claude-mem` | claude-mem | LLM provider 抽象设计 |
| `vendor/memsearch` | memsearch | openclaw plugin 三工具 + 两 hook 模板 |
| `vendor/claude-context` | claude-context | session-start hook 注入思路 |
| `vendor/engram` | engram | mem_* 工具命名 + 19 工具 schema 草案 |

详细 fork 来源见 [模块来源](module-fork-map.md)。

## .github/workflows/

```
docs.yml      # push 到 main + docs/** 改动 → 构建 mkdocs → 部署 gh-pages
```

详细配置见根 `.github/workflows/docs.yml`。

## 重要路径速查

| 想找 | 看 |
|---|---|
| CLI 所有子命令 | `memoryd/src/memoryd/cli.py` (`main()` 函数) |
| frontmatter 字段定义 | `memoryd/src/memoryd/schema.py` (`Frontmatter`) |
| SQLite 表 | `memoryd/src/memoryd/migrations/*.sql` |
| 知识图谱抽取 prompt | `memoryd/src/memoryd/llm/prompts/extract_entities.py` |
| identity 重写 prompt | `memoryd/src/memoryd/llm/prompts/rewrite_identity.py` |
| MCP 工具列表 | `memoryd/src/memoryd/mcp_server.py` (`build_server()`) |
| Web 路由 | `memoryd/src/memoryd/web/routes.py` |
| CC hook 脚本 | `plugins/claude-code/session-end.sh` |
| Codex notify wrapper | `plugins/codex/notify-wrapper.sh` |
| OpenClaw plugin 入口 | `plugins/openclaw/src/index.js` |
