---
title: 项目概览
keywords: memory-system, 本地记忆, 跨工具记忆, 用户画像, 痛点
---

# 项目概览：为什么要造 memory-system

## 它要解决的问题

主流 AI harness（Claude Code / Codex / OpenClaw / Cursor / ...）各自维护自己的"记忆"：

- Claude Code 有 `CLAUDE.md` + `~/.claude/projects/*/memory/`
- Codex 有 `AGENTS.md`
- OpenClaw 有自己的 memory-core 子系统
- 各种 MCP memory server（`mcp-memory-service` 等）各自存格式

结果就是：

1. **同一件事得在每个工具里重新教一遍**。今天用 CC 教它"我偏好 Solid 不偏好 React"，明天换 Codex 它一无所知。
2. **换设备等于换大脑**。MacBook 上累积的记忆，Linux 工作站上从零开始。
3. **AI 自己说的也被自动当成事实**。LLM 一句胡话被保存为长期偏好，下次召回出来污染上下文。
4. **隐私无法分级**。涉及客户信息 / 凭证 / 财务的内容和普通笔记混在一起。
5. **记忆只是文本堆，不是图**。问"我最近接的客户有哪些"系统不知道"客户"指什么。

## memory-system 给出的答案

| 痛点 | 解决方案 |
|---|---|
| 三端各自记忆 | 同一份本地 memoryd 后端 + 三端原生 capture 路径 |
| 换设备等于换大脑 | Markdown SoT + memories.json 标准格式 + 自配云盘同步 |
| AI 胡话污染 | 工作记忆 → DURA 4 准则 LLM 评分 → 用户审批 → 长期记忆 |
| 隐私无法分级 | scope 维度 + sensitive marker + AES-256-GCM + 授权访问 + append-only 审计链 |
| 记忆只是文本堆 | 自动抽 entities + relations + supersedes 演化 + N-hop 子图查询 |
| AI 不"认识"我 | weekly LLM 重写 `identity.md` + 月度变化报告 + trends digest |

## 它**不**做的事

- **不是云服务**。所有数据默认在本机；同步走用户自己的云盘。
- **不接管 AI 工具原生的记忆**。CC 的 `CLAUDE.md`、Codex 的 `AGENTS.md`、OpenClaw 的 memory-core 都照常工作，memoryd 是叠加的、独立的一层。
- **不做多用户协作**。一台机器只服务一个人。
- **不支持 CC / Codex / OpenClaw 之外的 harness**。其他工具想接也只能调通用 MCP server，不会为它写专门通路。

## 适合谁

- 你**经常切换** AI 工具（CC + Codex 同时用，或换公司换工具栈）
- 你**用多台机器**做开发
- 你希望 AI 能**记得你**而不是每次都得重新教
- 你愿意接受"本地优先"的代价：自己装环境、自己配同步盘、自己看 digest 审批

## 不适合谁

- 你只用一个 AI 工具，记忆从不跨工具
- 你只用一台机器
- 你希望 AI 是**完全无状态**的（每次重新输入上下文，不要它"懂你"）
- 你需要团队协作记忆库（这是另一个系统的问题）

## 顶层架构一句话

> Markdown 是 source of truth，SQLite 是索引，向量是搜索副产物，
> 知识图谱是自动学习的副产物，identity.md 是 LLM 周期性凝练后的"未来的我看到的当下的我"。

继续看 [安装](installation.md) 上手。
