---
title: 故障排查
keywords: 故障, 排查, 日志, 不工作, 调试
---

# 故障排查：常见问题速查

## 日志位置

```
~/.local/share/memoryd/logs/
├── cc-session-end.log         # Claude Code SessionEnd hook
├── codex-notify.log           # Codex notify wrapper 实时
├── openclaw-events.log        # OpenClaw plugin SDK 事件
├── mirror.stdout.log          # launchd FS-watch 守护 stdout
├── mirror.stderr.log          # launchd FS-watch 守护 stderr
└── ...

~/.local/share/memoryd/probe/
└── notify-probe.log            # Codex notify-probe 一次性诊断
```

## CC 会话结束后没 capture

```bash
# 1. hook 是否注册成功？
cat ~/.claude/settings.json | jq '.hooks.SessionEnd'

# 2. 脚本可执行？
ls -l /Users/abble/memory-system/plugins/claude-code/session-end.sh

# 3. 看 hook 日志
tail -f ~/.local/share/memoryd/logs/cc-session-end.log

# 4. 手工跑一遍（拿一份现成 transcript）
plugins/claude-code/session-end.sh /path/to/last-transcript.jsonl

# 5. 看 memoryd 是否在 PATH 或 venv 里
which memoryd
/Users/abble/memory-system/memoryd/.venv/bin/memoryd --version
```

## Codex 没捕获

```bash
# 1. notify wrapper 注册成功？
grep notify ~/.codex/config.toml

# 2. 实时通路日志
tail -f ~/.local/share/memoryd/logs/codex-notify.log

# 3. FS-watch 守护在跑？
launchctl list | grep memoryd       # macOS
systemctl --user list-timers        # Linux

# 4. 单次扫描诊断
memoryd mirror --codex --once

# 5. probe Codex notify 实际传什么
memoryd setup swap-codex-notify --to probe
# 跑一轮 Codex turn
tail ~/.local/share/memoryd/probe/notify-probe.log
# 看完切回 wrapper
memoryd setup swap-codex-notify --to wrapper
```

## OpenClaw 没捕获

```bash
# 1. plugin 加载？
openclaw plugins list | grep memoryd-openclaw

# 2. 授权了吗
openclaw config get plugins.entries.memoryd-openclaw.hooks.allowConversationAccess

# 3. SDK 通路日志
tail -f ~/.local/share/memoryd/logs/openclaw-events.log

# 4. FS-watch 兜底
memoryd mirror --openclaw --once
memoryd list --source=openclaw --recent=5       # SDK
memoryd list --source=openclaw-fs --recent=5    # FS-watch
```

## MCP 工具不出现

```bash
# 1. 配置在对的文件
cat ~/.claude.json | jq '.mcpServers.memoryd'

# 不是 ~/.claude/.mcp.json，那个 CC 忽略 user-level servers
# 不是 ~/.claude/settings.json

# 2. memoryd-mcp 跑起来
memoryd-mcp --verbose
# 应该 stderr 出 "memoryd-mcp ready: transport=stdio tools=13 admin=False"

# 3. 重启 CC

# 4. CC 里 /mcp 看是否列出 memoryd
```

## 搜索返回空

```bash
# 1. 数据存在？
memoryd list --limit=5

# 2. ripgrep 装了？
which rg

# 3. SQLite 索引是否损坏
memoryd audit verify

# 4. 强制重建索引
memoryd rebuild-index
# 输出例：rebuild-index: 234 memories indexed (0 errors)

# 5. 单独跑 ripgrep
rg "your_query" ~/.local/share/memoryd/scopes/
```

## 向量搜索不工作

```bash
# 1. milvus-lite / pymilvus 装了？
uv pip list | grep -E 'pymilvus|milvus-lite'

# 2. ONNX runtime 装了？
uv pip list | grep onnxruntime

# 3. bge-m3 模型下载？
ls ~/.cache/memoryd/models/
# 首次 embed 会自动下载

# 4. 看错误
memoryd-mcp --verbose 2>&1 | grep -i embedding

# 5. 切到 OpenAI 备选
memoryd config set embeddings.provider openai
export OPENAI_API_KEY=sk-...
```

## DURA / KG 不工作（LLM 相关）

```bash
# 1. provider 配了？
memoryd config show | jq .llm

# 2. API key 在环境里
echo $ANTHROPIC_API_KEY | head -c 20

# 3. 手工触发一遍
memoryd list --recent=1                  # 拿 slug
memoryd analyze-session <slug>           # 看 stderr 有无错误

# 4. 切到 Ollama 走本地
memoryd config set llm.provider ollama
memoryd config set llm.model qwen2.5:7b
# 起 ollama serve
```

## 跨机同步冲突

```bash
# 1. 看冲突列表
memoryd sync status

# 2. 看 _conflicts 目录
ls ~/.local/share/memoryd/scopes/_conflicts/

# 3. diff 看哪个版本对
diff ~/.local/share/memoryd/scopes/_conflicts/<slug>-<fp>.md \
     ~/.local/share/memoryd/scopes/<scope>/<type>s/<slug>.md

# 4. 决定保留版本：
#    - 想 keep 本地：把 _conflicts 里那份 cp 到正确位置覆盖
#    - 想 keep sync 版（默认就这样）：删 _conflicts 文件
```

## sensitive scope 403

```bash
# 1. 是否真是 sensitive
ls ~/scopes/finance/.memoryd-sensitive

# 2. 当前有 grant？
ls ~/.local/share/memoryd/grants/

# 3. 重新 grant
memoryd grant ~/scopes/finance --duration once

# 4. Web 端 sensitive 永远 403（设计如此），只能走 CLI
memoryd show <slug>
```

## audit verify 报错

不要自己改 `~/.local/share/memoryd/audit/audit.jsonl`！

如果非要：

```bash
# 找到断链处
memoryd audit verify --verbose 2>&1 | grep "chain broken at"

# 真的需要重置（**会丢失审计历史**）
# 备份现有
cp ~/.local/share/memoryd/audit/audit.jsonl ~/audit-backup.jsonl
# 删
rm ~/.local/share/memoryd/audit/audit.jsonl
# 之后的写入会从 seq=1 重新开始
```

## 字符编码 / 中文乱码

frontmatter YAML 走 `allow_unicode=True`；markdown body 一律 UTF-8。

如果某条 import 进来的旧文件有 BOM 或 GBK：

```bash
file ~/.local/share/memoryd/scopes/<x>/sessions/<y>.md
iconv -f GBK -t UTF-8 broken.md > fixed.md
mv fixed.md broken.md
```

## venv 路径写死了

`plugins/claude-code/session-end.sh` 和 launchd plist 都引用了绝对路径。
如果你 clone 到的不是 `/Users/abble/memory-system`：

```bash
# 重新生成 launchd plist + cron
memoryd setup uninstall-cron --all
memoryd setup uninstall-launchd-mirror
memoryd setup auto-install
```

对于 CC hook 脚本：编辑 `~/.claude/settings.json` 改 `command` 字段的路径。

## "为什么我看到很多 jieba warning"

LLM 不可用时 KG 走 jieba 兜底，第一次加载会刷一次词典 warning。这是正常的。

要消除：配 `ANTHROPIC_API_KEY` 让 LLM 走主路径。

## 还是不行？

```bash
# 健康检查（MCP admin 工具）
MEMORYD_MCP_ADMIN=1 memoryd-mcp &
# 在另一个终端
python -c "
import asyncio
from memoryd.mcp_tools import admin
print(asyncio.run(admin.doctor()))
"
```

输出会列每个子系统状态：data_root / index_db / embeddings / llm / kg / sync。

提 issue 时建议附上：

- `memoryd --version`
- `memoryd config show`
- `memoryd doctor` 输出
- 出错时的 stderr
- 环境（OS / Python 版本）
