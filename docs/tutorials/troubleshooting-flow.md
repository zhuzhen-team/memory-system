---
title: 教程 08 · 故障诊断流
keywords: 故障, 排查, 诊断, debug, 日志, 不工作
---

# 教程 08 · 故障诊断流：从症状到根因

**目标：** 三个最常见的故障场景：CC hook 不触发 / MCP 工具不出现 / 搜索返回空，每个走一遍完整诊断流，知道遇到新问题用什么思路。

## 通用诊断三板斧

无论什么症状，先跑这三步排除环境问题：

```bash
# 1. 命令在 PATH 里？
which memoryd
which memoryd-mcp

# 2. 版本是不是预期？
memoryd --help | head -3

# 3. 数据目录在哪？里面有东西？
echo $MEMORYD_DATA_ROOT
ls -la ~/.local/share/memoryd/scopes/ 2>/dev/null | head -5
```

如果第 1 步找不到 → 没激活 venv 或没加 alias。看 [详细安装](../getting-started/installation.md) 的"验证安装"节。

## 场景 1：CC hook 不触发

### 症状

跑了一段 Claude Code 会话退出后，`memoryd list --limit=5` **没有新的 session**。

### 诊断

```bash
# 1. hook 是否注册到了 ~/.claude/settings.json？
cat ~/.claude/settings.json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("hooks",{}).get("SessionEnd"))'
```

**期望：**

```
[{'matcher': '*', 'hooks': [{'type': 'command', 'command': 'python3 ".../session-end.py"'}]}]
```

如果是 `None` → 没装。跑 `memoryd setup install-cc-hook`。

```bash
# 2. 脚本文件存在？可执行？
ls -l ~/memory-system/plugins/claude-code/session-end.py
```

**期望：** 大小非零、可读。

```bash
# 3. 看 hook 日志
ls -la ~/.local/share/memoryd/logs/cc-session-end.log 2>/dev/null
tail -30 ~/.local/share/memoryd/logs/cc-session-end.log
```

**情况判断：**

| 日志状态 | 含义 |
|---|---|
| 文件不存在 | hook 根本没被 CC 触发；CC 设置可能没生效，重启 CC |
| 文件有但全是错误 | 脚本内部出错，看错误堆栈 |
| 文件有正常 `captured -> ...` 行 | hook 跑了，去 `memoryd list` 找对应 slug |

```bash
# 4. 手工模拟 hook 跑一遍
echo '{"session_id":"diag-test","transcript_path":"","cwd":"'"$(pwd)"'"}' \
  | memoryd capture --source=claude-code
```

**期望：** `captured -> /Users/<you>/.local/share/memoryd/scopes/.../sessions/2026-XX-XX-diag-test.md`

如果这一步报错 → memoryd 本体有问题，跟 hook 无关。

```bash
# 5. memoryd 在 hook 看得到的 PATH 里？
which memoryd
# hook 是用 python3 调脚本，脚本里再调 memoryd
# 如果 memoryd 只在 venv 里，脚本需要绝对路径
```

如果 venv 不激活找不到 memoryd → 改 hook 命令为绝对路径：

```bash
python3 << 'EOF'
import json
from pathlib import Path
p = Path.home() / ".claude/settings.json"
d = json.loads(p.read_text())
d["hooks"]["SessionEnd"][0]["hooks"][0]["command"] = (
    'PATH="$HOME/memory-system/memoryd/.venv/bin:$PATH" '
    'python3 "/Users/<you>/memory-system/plugins/claude-code/session-end.py"'
)
p.write_text(json.dumps(d, indent=2, ensure_ascii=False))
EOF
```

## 场景 2：MCP 工具不出现

### 症状

CC 里 `/mcp` 命令看不到 `memoryd` 这一行；或者列出来但工具列表是空。

### 诊断

```bash
# 1. memoryd-mcp 能起来？
memoryd-mcp --verbose &
# stderr 应该输出 "memoryd-mcp ready: transport=stdio tools=13 admin=False"
kill %1
```

如果起不来 → 看错误。常见：`MEMORYD_DATA_ROOT` 路径不可写、缺依赖（如 milvus-lite Windows 不支持）。

```bash
# 2. 配置在对的位置？
cat ~/.claude.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("mcpServers",{}).get("memoryd"),indent=2))'
```

**期望：**

```json
{
  "command": "/Users/<you>/memory-system/memoryd/.venv/bin/memoryd-mcp",
  "args": [],
  "env": {}
}
```

**注意：** 必须是 `~/.claude.json`，**不是** `~/.claude/.mcp.json` 或 `~/.claude/settings.json`。CC 只读前者作为 user-level MCP server 来源。

```bash
# 3. command 是绝对路径？
which memoryd-mcp
# 把这个绝对路径写进 ~/.claude.json 的 command
```

```bash
# 4. 重启 CC
# 完全退出（不只是关窗口）再开
```

如果还是不行，开 CC 的 MCP 日志：

```bash
# macOS
tail -f ~/Library/Logs/Claude/mcp-server-memoryd.log
# 或路径
ls ~/Library/Logs/Claude/
```

## 场景 3：搜索返回空

### 症状

`memoryd search "<明明存在的词>"` 返回 0 行。

### 诊断

```bash
# 1. 数据真的在？
memoryd list --limit=5
```

如果 list 也空 → 数据目录变了或没 capture。看 `$MEMORYD_DATA_ROOT`。

```bash
# 2. ripgrep 装了？
which rg
```

memoryd search 全文路要 rg。没装 → 装。

```bash
# 3. SQLite 索引和 markdown 同步？
sqlite3 ~/.local/share/memoryd/index.db "SELECT COUNT(*) FROM memories"
find ~/.local/share/memoryd/scopes/ -name '*.md' | wc -l
```

两个数差别大 → 索引漂了。修：

```bash
memoryd rebuild-index
```

```bash
# 4. 向量库存在？
ls -la ~/.local/share/memoryd/vector.db
```

不存在或 0 字节 → 向量路没起，可能 milvus-lite 装失败（Windows）。这种情况搜索只走全文 + 知识图谱，准确度打折但不至于全空。

```bash
# 5. 加密 scope 没 grant？
memoryd search "<查询>" --include-forgotten
# 加这个看是否漂到 soft-forgotten 了
```

```bash
# 6. 看 search 内部命中
memoryd search "<查询>" --json
# 即使最终输出空，--json 也会输出三路命中的中间结果
```

## 你掌握了

- 三板斧前置环境检查
- 三个典型故障的完整诊断分支
- 通用思路：从配置 → 日志 → 内部状态 一层层往里挖
- 几个关键索引（`index.db`、`vector.db`、`logs/`）的位置

## 接下来

遇到没见过的故障，套同样的思路：

1. **先确认环境**（PATH / venv / 数据目录）
2. **看相关日志**（`~/.local/share/memoryd/logs/*.log`）
3. **手工跑最小复现命令**
4. **对比 SQLite 索引 vs markdown 真相**

完整故障速查表：[操作 · 故障排查](../operations/troubleshooting.md)。

[← 教程系列总目](index.md)
