---
title: FAQ
keywords: FAQ, 常见问题, 隐私, 数据存储, 卸载, 兼容性
---

# FAQ：真实用户问过的问题

按问题类型分组。Ctrl-F 找你关心的。

## 隐私与安全

### 我会不会泄露隐私？

memoryd **全程本地**：

- 默认无网络出站
- 数据落本机 `~/.local/share/memoryd/`
- LLM 调用是**可选**的（不配 API key 就完全离线）
- 走 LLM 的请求也是你自己的 API key，到你自己的 Anthropic / OpenAI / Ollama 账号

如果你担心 LLM provider 看见内容：用 Ollama 跑本地模型，或者完全不配 LLM。

### 数据存在哪里？

| 内容 | 位置 |
|---|---|
| markdown 记忆原文 | `~/.local/share/memoryd/scopes/<hash>/` |
| SQLite 索引 | `~/.local/share/memoryd/index.db` |
| 向量库 | `~/.local/share/memoryd/vector.db` |
| 用户画像 | `~/.local/share/memoryd/profile/identity.md` |
| 历次画像快照 | `~/.local/share/memoryd/profile/snapshots/` |
| 日志 | `~/.local/share/memoryd/logs/` |
| 加密 scope 的 .md.enc | 同样在 scopes 下，文件后缀 `.enc` |
| 密钥 | OS keyring（macOS Keychain / Linux Secret Service / Windows DPAPI），**不在文件系统里** |
| 配置 | `~/.config/memoryd/config.yaml` |

Linux XDG 标准；macOS 不严格走 XDG 但路径一致。

可以用 `MEMORYD_DATA_ROOT` 环境变量整体改根。

### 占多大磁盘？

经验数字（个人重度使用一年）：

- markdown：约 100–500 MB
- SQLite：约 30–100 MB
- 向量库：约 200 MB–1 GB（取决于 embedding 维度 / 条数）
- 总计：通常 < 2 GB

bge-m3 ONNX 模型本身首次下载 600 MB 左右，在 `~/.cache/huggingface/`。

### 卸载会删数据吗？

**不会**。卸载流程：

```bash
memoryd setup uninstall-cron --all      # 卸 cron / launchd / Task Scheduler
memoryd setup uninstall-launchd-mirror  # macOS 额外
# 数据原封不动留在 ~/.local/share/memoryd/

# 要删数据自己来（建议先备份）
memoryd sync export                      # 备份
rm -rf ~/.local/share/memoryd
```

完整流程见 [卸载](operations/uninstall.md)。

### AI 自己写的记忆我能审批吗？

可以。整个治理就为这个设计：

- 每条 session 都被 LLM DURA 评分
- 均分 ≥ 0.6 进 `promotions` 表 `pending` 状态
- 用户跑 `memoryd digest` 看待审清单
- `memoryd promote <id>` 批准 → 真生成长期记忆 .md
- 不批准就一直 pending，30 天后归到归档

不想要 LLM 评分就别配 API key —— 一切自动评分都跳过，只有手工 capture 的 manual 记忆。

### 标 sensitive 后是不是绝对安全？

不是绝对，是**抬高门槛**：

- 加密：AES-256-GCM，已经是工业标准
- 密钥：OS keyring（不在你硬盘明文）
- 授权：默认 deny，需要 grant 才能解密
- 审计：每次访问都进不可篡改链

但如果攻击者拿到了**你的 OS 账号 + active 的 grant 窗口**，他能解密。这不是 memoryd 的失败，是 OS-level 防护已经被攻破的前提。

详见 [加密](operations/encryption.md)。

## 兼容性与生态

### 能跟 `mcp-memory-service` 一起用吗？

可以。memoryd 是叠加的：

- `mcp-memory-service` 跑自己的，你 CC 里两个 MCP server 都注册
- 工具名不重叠（memoryd 是 `mem_*`，前者是 `memory_*`）
- 数据互不打架

只是会比较两套记忆系统都自己存一份，磁盘占用翻倍。

### 接管 CC 的 CLAUDE.md / Codex 的 AGENTS.md 吗？

**不接管**。这两个文件 CC / Codex 自己读，memoryd 不动它们。memoryd 是独立一层，跟原生记忆并存。

如果你想把现有 `CLAUDE.md` 内容导进 memoryd：

```bash
memoryd import claude-md ~/.claude/CLAUDE.md
```

会把每段拆成对应 type 的长期记忆，但**不删源文件**。

### 停掉 LLM API 还能用吗？

能。capture / list / search / show / digest / web / kg / profile show 全部继续工作。

跳过的只是：

- DURA 评分（自动入 promotions 这一步跳过）
- 实体抽取（降级到 jieba 中文分词）
- supersedes 检测（confidence 算不出来）
- weekly identity 重写

中文场景 jieba 兜底还行；英文场景不配 LLM 实体抽取效果会差。

### memories.json 兼容什么？

向后兼容 v5 schema（[mcp-memory-service](https://github.com/doobidoo/mcp-memory-service) 的导出格式）：

```bash
memoryd import memories-json ~/Downloads/memories.json
```

会把 v5 条目映射到 memoryd 的六类型。详见 [memories.json 格式](reference/memories-json.md)。

### Windows 支持怎么样？

二级支持。具体限制：

- `milvus-lite` 在 Windows 上**不支持** → 向量搜索路自动跳过，只走全文 + 知识图谱
- launchd / cron 自动切到 Task Scheduler
- 路径用 `%LOCALAPPDATA%\memoryd\` 而不是 `~/.local/share/memoryd/`
- 推荐用 WSL2 + Ubuntu 跑，体验跟 Linux 完全一致

### 我能在服务器上跑吗（headless）？

可以。memoryd 不需要 GUI；只是：

- `keyring` 在无头 Linux 上需要 `gnome-keyring-daemon` + dbus，或者走 passphrase 模式
- web dashboard 绑 127.0.0.1，远程访问需要 SSH 端口转发：`ssh -L 18765:127.0.0.1:18765 user@server`

## 工作机制

### 数据怎么从一台机器搬到另一台？

走 [跨设备同步](tutorials/cross-device-sync.md)。

不愿意配同步：

```bash
# 备份
rsync -av ~/.local/share/memoryd/ /tmp/memoryd-backup/

# 新机器
rsync -av /tmp/memoryd-backup/ ~/.local/share/memoryd/
memoryd rebuild-index    # 索引重建
```

但敏感 scope 密钥不在备份里，要单独 export-key / import-key。

### 诺基亚式恢复：硬盘炸了换新机怎么走

前提是你早就开了同步：

1. 新机器装 Python / uv / ripgrep
2. clone 仓库 + uv pip install
3. 配同步盘指向云盘
4. `memoryd sync import` → 拿回所有 markdown 并自动 rebuild-index

如果是敏感 scope，你还要事先把密钥导出到（你能恢复的）安全位置。否则 .md.enc 永久解不开。

### weekly identity 重写到底改了我的什么

只改 `~/.local/share/memoryd/profile/identity.md`（以及落一个新 version 到 SQLite 表）。**不改你的记忆数据**。

你随时可以：

- `memoryd profile diff` 看变化
- `memoryd profile history` 列历次
- 不喜欢这次：手工编辑 identity.md，下次 weekly 又会重新覆盖（除非你也改 last_modified 的判定）

或干脆关 weekly：

```bash
memoryd setup uninstall-cron --weekly-identity
```

### vendor/ 目录里那些仓我能删吗？

仓库**不带** `vendor/`。早期设计阶段一度用过 git submodule fork 几个老仓，现在全砍了 —— 所有模块都在 `memoryd/src/memoryd/` 下直接维护。

如果你从老版本升上来还留着 `vendor/`：直接 `rm -rf vendor/` 不影响。

### 为什么会自动加密一些 scope？

memoryd **不会自动**加密。除非：

- 你显式跑了 `memoryd mark-sensitive <path>`
- 或父目录有 `.memoryd-sensitive` 标记，新建 scope 会继承

没有这两条触发，所有记忆都是明文 markdown。

### 三端可以同时用吗？

可以，这是 memoryd 的核心场景。

- 三端各自的 capture 入口写到同一份 `~/.local/share/memoryd/`
- 三端通过 MCP server 读同一份记忆
- frontmatter 里的 `source` 字段标明来源（claude-code / codex / openclaw / manual / ...）

详见三个集成页：
- [Claude Code](integrations/claude-code.md)
- [Codex](integrations/codex.md)
- [OpenClaw](integrations/openclaw.md)

## 性能与可靠性

### 第一次 capture 很慢，正常吗？

正常。首次会下载 bge-m3 ONNX 模型（约 600 MB）和 jieba 词典。一次性。

后续 capture 在毫秒级；DURA 评分异步 fork，不阻塞写入；LLM 调用 200ms–几秒，看 provider。

### 进程会一直跑后台吗？

**默认不会**。memoryd 是**事件驱动**：

- 来个 capture / search → 起进程 → 干完退出
- cron 触发 decay-sweep / digest / weekly identity
- launchd / systemd 的 watch 守护（如果你装了 mirror）会常驻

`memoryd web` 启 dashboard 时是 uvicorn 常驻；不打开就不在。

### memoryd 崩溃会丢数据吗？

写入流程是 `markdown 先落地 + fsync → SQLite 后写`。markdown 是 SoT；即使 SQLite 写一半挂了，再起来跑 `memoryd rebuild-index` 一句话回来。

对你来说：可见症状最多是"几条记忆暂时搜不到"，重建索引后即恢复。

### 一次 capture 失败会重试吗？

会 best-effort。`session-end.py` 脚本即使 memoryd 退出码非 0 也不阻塞 CC（hook 是 fire-and-forget）。

要补救：拿到 transcript_path 手动跑：

```bash
memoryd analyze-session <slug>
```

## 还没回答到？

- [操作 · 故障排查](operations/troubleshooting.md) 速查表
- [教程 08 · 故障诊断流](tutorials/troubleshooting-flow.md) 系统化思路
- GitHub Issues：<https://github.com/zhuzhen-team/memory-system/issues>
