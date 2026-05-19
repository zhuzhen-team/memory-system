---
title: 教程 06 · 跨设备同步
keywords: sync, 同步, iCloud, Dropbox, git, 多设备, 换电脑
---

# 教程 06 · 跨设备同步：iCloud / Dropbox / git 走起

**目标：** 把 macOS 上的记忆带到 Linux 工作站 / 新机器，跑通"打 sync export → 换机器 → sync import"。

**前置：** 至少一台机器已用一段时间、有可用的云盘或 git 仓库。

## memoryd 同步设计

memoryd **不内置云服务**。设计思想：

- 你已经有 iCloud / Dropbox / OneDrive / Google Drive
- 你已经会用 git
- memoryd 只提供 export / import 两条命令把记忆**夹进**这些工具

数据流：

```
机器 A 的 ~/.local/share/memoryd/scopes/
     ↓ memoryd sync export
~/iCloud/memoryd-sync/                     (或 git push)
     ↓ memoryd sync import
机器 B 的 ~/.local/share/memoryd/scopes/
```

只有 markdown 进同步盘。SQLite / 向量库 / 知识图谱**不同步** —— 它们都是 markdown 派生出来的索引，到对端 `rebuild-index` 一句重建。

## 一、首次配置（机器 A）

```bash
memoryd config set sync.dir ~/Library/Mobile\ Documents/com~apple~CloudDocs/memoryd-sync
memoryd config set sync.enabled true
```

或 Dropbox / 别的：

```bash
memoryd config set sync.dir ~/Dropbox/memoryd-sync
```

或 git 仓库：

```bash
mkdir ~/memoryd-sync && cd ~/memoryd-sync && git init
memoryd config set sync.dir ~/memoryd-sync
# 后面手动 git add + commit + push
```

## 二、第一次 export

```bash
memoryd sync export
```

**预期输出：**

```
exported 234 files to ~/Library/Mobile Documents/com~apple~CloudDocs/memoryd-sync
scopes: 12  conflicts: 0  skipped: 0
```

mirror 所有 `.md` 进同步盘，保持目录结构：

```
memoryd-sync/
├── scopes/
│   ├── db8435ffd199/
│   │   ├── sessions/
│   │   ├── decisions/
│   │   └── ...
│   └── <other_scope>/
└── profile/
    └── identity.md
```

## 三、自动 export

每次会话结束顺手 export：

```bash
memoryd config set sync.auto_export_on_session_end true
```

之后 `memoryd capture` 完会接着跑 `memoryd sync export --auto`（增量、只动改过的）。

## 四、换机器：在机器 B 装好 memoryd

按 [详细安装](../getting-started/installation.md) 装好 memoryd（同样 venv、同样 LLM 配）。

## 五、机器 B 上配同步盘

```bash
memoryd config set sync.dir ~/Dropbox/memoryd-sync
# 注意路径——同一个云盘在不同 OS 路径不一样
```

## 六、首次 import

```bash
memoryd sync import
```

**预期输出：**

```
imported 234 files
scopes: 12  conflicts: 0  index rebuilt
```

发生的事：

1. 拷贝所有 `.md` 到 `~/.local/share/memoryd/scopes/`
2. 自动 `rebuild-index`（SQLite + 向量 + 知识图谱全重建）
3. 冲突文件落到 `_conflicts/` 目录（同 slug 不同内容时）

## 七、看状态

```bash
memoryd sync status
```

**预期输出：**

```
| scope_hash    | local_files | sync_files | last_export        |
|---------------|-------------|------------|--------------------|
| db8435ffd199  | 45          | 45         | 2026-05-20T10:23   |
| ab12cd34ef56  | 12          | 12         | 2026-05-20T10:23   |
| ...           |             |            |                    |

conflicts: 0
```

每个 scope 本地 vs 同步盘的条数；不一致提示有未 export / 未 import。

## 八、git 同步的额外步骤

云盘走 cron 触发；git 要你手动 commit + push：

```bash
cd ~/memoryd-sync
git add -A
git commit -m "memoryd snapshot $(date +%F)"
git push
```

机器 B：

```bash
cd ~/memoryd-sync
git pull
memoryd sync import
```

git 的好处：有完整版本历史，能 diff、能 revert、能 blame 谁加了哪条记忆。

## ⚠ scope_hash 跨平台 caveat

```
~/projects/foo  在 macOS → /Users/<u>/projects/foo  → hash A
~/projects/foo  在 Linux → /home/<u>/projects/foo   → hash B
```

memoryd 看物理路径算 hash。同一个项目在两台 OS 上**会算成两个 scope**。

应对：在配置里手工设 alias（详见 [同步配置](../operations/sync-setup.md) 跨平台 scope 节）。

## 九、敏感 scope 的同步

`.md.enc`（已加密的）正常同步。但**密钥不在同步盘里** —— 密钥在每台机器的 OS keyring。换机器要：

```bash
# 机器 A 导出密钥（在加密 scope 根目录跑）
memoryd export-key > /tmp/key.txt  # 走安全通道传到 B
# 机器 B 导入
memoryd import-key < /tmp/key.txt
```

详见 [教程 07 · 敏感记忆](sensitive-memories.md)。

## 你掌握了

- export / import 完整流程
- 自动 export on session end
- git vs 云盘各自的取舍
- 跨平台 scope_hash 陷阱
- 敏感记忆的密钥分发

## 下一步

[教程 07 · 敏感记忆](sensitive-memories.md) —— 涉及客户信息、凭证、财务怎么单独保护。
