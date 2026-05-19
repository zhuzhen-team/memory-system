---
title: 教程系列
keywords: 教程, 学习路径, tutorials
---

# 教程系列：从写第一条记忆到全功能上手

下面 9 篇教程按学习路径排列。**每篇 5–15 分钟**，全部带可复制的命令 + 预期输出。

如果你完全没用过 memoryd，请按顺序读完前三篇；后面 6 篇按需挑。

## 学习路径

### 🌱 入门三件套（按顺序读）

1. **[第一次记忆](first-memory.md)** —— 手动 capture 一条 + 查回来。理解写入与召回的最小闭环。
2. **[自动捕获](auto-capture.md)** —— 挂上 Claude Code SessionEnd hook，让 AI 自己往里写。
3. **[搜索与召回](search-and-recall.md)** —— keyword / 向量 / 混合，何时用哪种。

### 🌿 进阶六题（按需挑）

4. **[知识图谱](knowledge-graph.md)** —— 系统自动从你记忆里抽实体、画关系图，N-hop 反查。
5. **[画像自学习](profile-self-learning.md)** —— 看 `identity.md` 从 0 长出来：未来的你回看现在的你。
6. **[跨设备同步](cross-device-sync.md)** —— Markdown 走 iCloud / Dropbox / git，多机器接力。
7. **[敏感记忆](sensitive-memories.md)** —— 标敏感 / AES-256 加密 / 授权访问 / 审计链。
8. **[故障诊断流](troubleshooting-flow.md)** —— 出问题怎么一步步定位到根因。

## 跟着教程能学到什么

| 维度 | 你会理解 |
|---|---|
| **数据流** | 一条记忆从 SessionEnd 事件 → 落 `.md` → 进 SQLite → 进向量库 → 进知识图谱 全程 |
| **治理** | DURA 评分 → 工作记忆升长期 → 衰减 → 重复合并 → supersedes 决策演化 |
| **集成** | Claude Code（hook + MCP）/ Codex（notify wrapper）/ OpenClaw（plugin） 各端怎么挂 |
| **隐私** | scope 维度 + sensitive marker + AES-256 + OS keyring 怎么协作 |
| **运维** | cron / launchd / systemd timer / Task Scheduler 自动化 |

## 学完之后

- [架构全景](../architecture/overview.md) —— 看整体设计为什么这么定
- [CLI 命令](../reference/cli.md) —— 所有命令速查
- [MCP 工具](../reference/mcp-tools.md) —— 19 个 `mem_*` 工具签名
- [FAQ](../faq.md) —— 20+ 个真实用户问过的问题
