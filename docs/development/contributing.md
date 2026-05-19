---
title: 贡献指南
keywords: 贡献, contributing, PR, commit, 风格
---

# 贡献指南

memoryd 是个人作品，PR 与 issue 都欢迎。下面是协作约定。

## 设置开发环境

```bash
git clone https://github.com/zhuzhen-team/memory-system ~/memory-system
cd ~/memory-system/memoryd
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

跑测试确认环境 OK：

```bash
uv run pytest -v
```

## Commit 风格

- 标题用中文（项目沟通语言）
- 代码本身用英文
- 标题格式 `<scope>: <jobs to be done>` 例如 `kg: supersedes 阈值统一到常量`
- body 写"为什么"，不写"做了什么"（diff 已经说了做了什么）

例子：

```
kg: supersedes 阈值统一到 SUPERSEDE_AUTO_THRESHOLD

之前 detect_supersedes_for_new_memory 里硬编码 0.85，supersedes.py
和 prompts/judge_supersedes.py 又各自定义阈值。容易漂。
本次统一到 SUPERSEDE_AUTO_THRESHOLD / SUPERSEDE_REVIEW_THRESHOLD。
```

## PR

- 单 PR 单主题。重构 + 新功能不要混
- PR 描述写动机 + 设计权衡 + 测试覆盖
- 链相关 issue（如果有）

## 代码风格

- Python 用 `ruff format` 格式化
- 静态检查 `ruff check`
- 类型注解尽量加（重要 API 必须加）
- 函数 docstring 用中文（多人参与时英文也可）
- 模块顶 docstring 一定写清楚这个模块的责任和契约

## 加新功能的常见流程

1. **看现有模块边界**：[仓库结构](repo-layout.md)。新功能属于哪一层？
2. **先写测试**：TDD 风格，先表达想要的行为
3. **小步实现 + 跑测试**
4. **改文档同步**：`docs/` 里对应页要更新
5. **本地跑 `mkdocs build --strict`** 确认文档没破

## 文档约定

- 全中文（用户面文档）
- 代码 / 路径 / 标识符英文不翻译
- H1 标题用自然语言而非全大写
- 多用 mermaid 图（流程 / 时序 / 状态）
- 引用源码用相对仓库路径 + 行号链接到 GitHub
- 每篇头部加 frontmatter（title + keywords），方便搜索

## 文档预览

```bash
cd ~/memory-system
uv pip install mkdocs mkdocs-material mkdocs-macros-plugin mkdocs-mermaid2-plugin pymdown-extensions
mkdocs serve --dev-addr 127.0.0.1:8000
# 浏览器 http://127.0.0.1:8000/
```

构建检查（无 warning 才合格）：

```bash
mkdocs build --strict
```

## 改动数据 schema

涉及 SQLite 表结构改动：

1. 加 `migrations/00X_<name>.sql`（不动现有 migration）
2. `memoryd rebuild-index` 测试 migration 跑得通
3. 写测试覆盖新表的读写
4. 改文档：[数据模型](../reference/data-model.md)

涉及 frontmatter 字段：

1. 改 `schema.py` 的 Pydantic `Frontmatter`，**新字段必须 optional**（向后兼容老 .md）
2. 在 `to_markdown()` 里决定是否输出（默认值不输出，保持文件清爽）
3. 改文档：[数据模型](../reference/data-model.md)

## 改 MCP 工具

涉及 `mem_*` 工具：

1. 在 `mcp_tools/<group>.py` 写 async handler
2. 在 `mcp_server.py` 的 `build_server()` 注册 fastmcp 装饰器
3. 测试用 `build_server(include_admin=...)` + `list_tool_names()` 验证注册
4. 改文档：[MCP 工具](../reference/mcp-tools.md)

新工具要决定 tier（agent / admin），admin 工具默认隐藏。

## 改三端集成

涉及 `plugins/`：

- CC hook 脚本改动要在 macOS / Linux / Windows 三平台试
- Codex notify wrapper 改前先跑 probe 看当前 Codex 版本传啥
- OpenClaw plugin 改前看 OpenClaw SDK 最新版本

## 故障

提 issue 时带上：

- OS 版本 + Python 版本
- `memoryd --version`
- `memoryd config show`
- 错误时的 stderr + 相关日志（`~/.local/share/memoryd/logs/`）
- 复现步骤

## 行为准则

- 不带情绪、不人身
- 不要求别人做你不愿意做的工作（写测试 / 写文档）
- 设计争议先把双方观点写清楚再讨论
- 接受"不合并"是合理结果

## 路线

memoryd v1.0 已完成 v1 spec §4 全部 32 项功能。未来方向（按可能性递减）：

- **v1.1**：sensitive scope 跨设备同步配套（passphrase rotation / 多 scope 共享 key）
- **v1.2**：scope_hash 从 git remote 派生（解决跨平台路径不一致）
- **v1.3**：Web Dashboard 可编辑（trade-off：增加复杂度）
- **v2.0**：Obsidian / Basic Memory 双向同步（基于已对齐的 Basic Memory schema）

不在路线上的：

- 云后端（违背本地优先）
- 团队协作记忆库（另一个系统的问题）
- 其他 harness 支持（CC / Codex / OpenClaw 之外）
