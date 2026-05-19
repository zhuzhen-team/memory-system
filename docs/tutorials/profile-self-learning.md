---
title: 教程 05 · 画像自学习
keywords: identity, profile, 画像, 自学习, weekly rewrite, change report
---

# 教程 05 · 画像自学习：让系统学你

**目标：** 看 `identity.md` 从 0 一周一周长出来，理解 weekly rewrite + 月度变化报告，知道怎么 diff、回查、手动重写。

**前置：** 库里跑了至少一周、配了 LLM（不配 LLM 不会自动重写）。

## 它在干什么

每周一 09:00（cron 默认），memoryd 会：

1. 拉本周所有 long-term 记忆（decision / preference / fact / playbook / warning）
2. 拉 recall_count ≥ 5 的高频被读记忆（哪怕是 session 类型）
3. 拉最近一次 `identity.md` 作为基线
4. **喂给 LLM 重写**：写一份 "面向未来的我，介绍当下的我" 的文档
5. 落到 `~/.local/share/memoryd/profile/identity.md`
6. 旧版本进 SQLite `profile_versions` 表保存快照
7. 月底另跑一次 `change-reports/YYYY-MM.md` 总结这个月画像变化

## 一、看当前画像

```bash
memoryd profile show
```

**库里啥都没有时输出：**

```
(尚无 identity.md — 跑 `memoryd profile rewrite` 生成首版)
```

**有内容后类似：**

```markdown
# 王某（开发者）

## 角色与背景
- 主语言：Python / TypeScript
- 主工具：Claude Code + Codex + memoryd
- 当前在做：memory-system，本地优先的个人记忆系统

## 偏好
- 函数式 > OOP
- Solid > React（2026-03 迁移完成）
- pytest + ruff + hatchling 工具链

## 决策模式
- 重要决策都会先列利弊
- ...
```

## 二、手动触发首次重写

第一次跑、不想等一周：

```bash
memoryd profile rewrite
```

**预期输出：**

```
profile rewrite: tokens_in=8421, tokens_out=1203, version=1
written /Users/<you>/.local/share/memoryd/profile/identity.md
```

需要 LLM。没配的话报错退出。

## 三、看历次快照

```bash
memoryd profile history --limit=10
```

**预期输出：**

```
| version | created_at          | reason              | tokens_out |
|---------|---------------------|---------------------|------------|
|       3 | 2026-05-19T09:00:01 | weekly_cron         |       1240 |
|       2 | 2026-05-12T09:00:00 | weekly_cron         |       1180 |
|       1 | 2026-05-08T14:23:15 | manual_first_rewrite|       1120 |
```

每次重写都进表。

## 四、diff 两个版本

```bash
memoryd profile diff --from=2 --to=3
```

**预期输出：**

```diff
@@ ## 偏好 @@
- ...
- React > Vue（如果一定要写前端）
+ Solid > Vue（如果一定要写前端）
+ React 不再推荐（2026-03 迁移）

@@ ## 当前在做 @@
- memory-system 设计阶段
+ memory-system v1.0 发布；规划 v1.1 多设备同步
```

git-style diff，直接告诉你"系统对你的认识"发生了什么变化。

## 五、月度变化报告

每月 1 号自动生成 `~/.local/share/memoryd/profile/change-reports/2026-05.md`。手动跑：

```bash
memoryd profile report --month=2026-05
```

**输出文件结构：**

```markdown
# 2026 年 5 月画像变化

## 新增的事实
- 决定迁移到 Solid（2026-05-15，置信度 0.91）
- ...

## 被替换的旧决策
- "React 是主力前端" → "Solid 是主力前端"
- ...

## 重要新实体
- memoryd（首次出现：2026-05-01，本月被提及 38 次）
- ...

## 你的 5 月一句话
- ...（LLM 总结）
```

## 六、趋势页

```bash
memoryd profile trends --window-days=90
```

**预期输出：**

```
- 总记忆条数：234（+45 vs 上 90 天）
- 长期记忆占比：18%（+3pp）
- 最活跃 scope：~/memory-system（112 条）
- 实体提及增长 Top 3：memoryd (+25), Solid (+12), MCP (+8)
- 决策频率：本期 12 条 vs 上期 8 条
```

## 七、Web dashboard 上的 identity

```bash
memoryd web --port=18765
# 打开 /identity
```

- 顶部：当前 identity.md 渲染
- 中部：历次版本时间轴
- 底部：本月 change-report 的关键变化卡片

## 你掌握了

- weekly rewrite + monthly report 周期性流程
- diff 两个版本看系统对你的认识演化
- 手动 rewrite / report / trends 三个出口
- 跟 [知识图谱](knowledge-graph.md) 的关系：图是原料，identity.md 是凝练

## 下一步

数据攒在一台机器上没意义 → [教程 06 · 跨设备同步](cross-device-sync.md)。
