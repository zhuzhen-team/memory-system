# 三端会话捕获 (Tri-Client Session Capture) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 端到端验证：同一项目目录下，**Claude Code、Codex、OpenClaw 三端**任意一端的会话结束后写入的工作记忆，另外两端在新会话里能通过 `search_memory` 工具召回。Plan 1 已覆盖 CC 一端；本 plan 把 capture 通路扩展到 Codex（hooks.json）和 OpenClaw（TypeScript 插件）。

**Architecture:** 复用 Plan 1 已有的 `memoryd` Python 包（schema / storage / search / MCP server / capture CLI）和 scope 解析逻辑——三端写入同一份 Markdown 数据根，scope_hash 由 cwd 决定，跨工具自然共享。只新增两件事：(1) Codex 的 `~/.codex/hooks.json` 在 `Stop` 事件上注册一个 bash 脚本（与 `cc-session-end-hook.sh` 镜像），通过 `--source codex` 把来源标记进 frontmatter；(2) 一个 OpenClaw TS 插件包，在 `agent_end` lifecycle hook 上把会话内容 JSON 化后管道给 `memoryd capture --source openclaw`。CLI 加一个 `--source` 旗标即可，schema 不变（`Frontmatter.source` 早就是 `str`，扩展是数据约定不是类型约定）。

**Tech Stack:** Python 3.11+（沿用 memoryd 包）、bash（Codex hook 脚本沿用 Plan 1 模式）、TypeScript / Node（OpenClaw 插件 SDK 要求）、`pytest`（Python 测试）、`vitest` 或 `node --test`（TS 插件最小单测；选 `node --test` 避免新增依赖）。

**Decomposition Note:** 这是 8 plan 中的第 2 个。Plan 1 已 merge（commit `2e7fb25`）。本 plan 不改 schema、不动 SQLite（推迟到 Plan 3）、不加密、不跨平台（推迟到 Plan 5）、不同步（推迟到 Plan 6）。只做"三端 capture + 跨端召回"这条主线。

**Research provenance:**
- Codex hooks.json schema：context7 `/openai/codex` 的 `codex-rs/hooks/src/engine/config.rs` 和 `codex-rs/core/tests/suite/hooks.rs`——确认 `hooks.json` 路径在 `~/.codex/` 下；`Stop` 事件支持 `command` 类型 handler；`async: true` 可让 hook 不阻塞 Codex 退出。
- OpenClaw plugin SDK：context7 `/openclaw/openclaw` 的 `docs/plugins/hooks.md`、`docs/plugins/sdk-overview.md`、`docs/concepts/agent-loop.md`——确认插件用 `api.on(hookName, handler, opts?)` 注册；`agent_end` 是 post-completion 钩子；安装走 `openclaw plugins install --force <path>`；要 `allowConversationAccess` 权限才能读 turn 数据。
- memsearch 三端实操经验：`docs/detailed-plan.md` §4.1 + `记忆系统设计/01-openclaw-强agent记忆机制.md`——已总结 Codex `transcript_path` 可能不稳、子进程 `codex exec` 不应触发递归 hook、OpenClaw 飞书渠道等非默认 agent 模式可能拿不到完整 session 数据。本 plan 不假设这些边界，但记录为"e2e 后置观察项"。

---

## 文件结构

执行本 plan 后会产生 / 修改这些文件（所有路径都从 repo 根 `/Users/abble/project-management-personal/` 算起）：

| 路径 | 责任 | 操作 |
|---|---|---|
| `memoryd/src/memoryd/cli.py` | 加 `--source` 旗标给 `capture` 子命令 | Modify |
| `memoryd/tests/test_cli.py` | 加 `--source` 行为测试 | Modify |
| `scripts/codex-stop-hook.sh` | Codex Stop 事件 hook 脚本 | Create |
| `scripts/openclaw-memoryd-plugin/package.json` | OpenClaw 插件 npm manifest | Create |
| `scripts/openclaw-memoryd-plugin/src/index.ts` | OpenClaw 插件入口（注册 `agent_end`） | Create |
| `scripts/openclaw-memoryd-plugin/README.md` | 插件安装 & 权限说明 | Create |
| `scripts/openclaw-memoryd-plugin/tests/index.test.mjs` | 最小 Node test（payload 序列化正确） | Create |
| `memoryd/README.md` | 加 Codex + OpenClaw wire-up 章节 | Modify |
| `docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt` | 实施进度日志（subagent 写） | Create |

**不在本 plan：**
- memsearch 的 fork / 引入——detailed-plan §4 提及，但本 plan 改用 DIY 的 Codex hook + OpenClaw plugin，避免上游适配阻塞。memsearch 留作未来对比方案。
- SQLite 索引（Plan 3）；加密（Plan 4）；Windows / Linux 适配（Plan 5）；多机同步（Plan 6）；Web UI（Plan 7）；旧记忆 import（Plan 8）。
- LLM 摘要——Plan 3 才替换朴素截断；本 plan 沿用 Plan 1 的截断逻辑（最近 50 条 message text，最多 2000 字符）。

**默认运行时数据目录：** `~/.local/share/memoryd/scopes/<scope-hash>/sessions/<YYYY-MM-DD>-<session-id>.md`（与 Plan 1 一致）。三端用同一个根 → 同一个 scope_hash → 自然共享。

---

## 风险与不确定性（先读再开工）

下面这些点在写 plan 时**已知**但**未在本地验证过**。实施 subagent 在对应 task 遇到这些信号时应停下问用户，不要硬推：

1. **Codex hooks 是否需要 `experimental.hooks.enabled = true` 之类的功能开关**：memsearch 文档提到"hooks feature flag 启用"。本 plan 在 Task 4 wire-up 步骤里加一个"检查 `~/.codex/config.toml` 是否有 hooks 相关 flag，若有则启用"的探测步，但具体 flag 名称在本 plan 写的时候未确定。subagent 在 Task 4 跑探测后，如果发现 Codex 未启用 hooks，**停下问用户**当前 Codex 版本和启用方式。
2. **Codex `Stop` 事件 payload 的精确字段**：context7 没给出 stdin JSON 格式。本 plan **假设** 与 CC SessionEnd 类似——含 `session_id`、`transcript_path`、`cwd` 至少这三个键。Task 2 的 hook 脚本设计上对未知字段宽容（透传整个 JSON 给 `memoryd capture`，CLI 已经能容错处理 missing key）；e2e Task 6 第一次跑会暴露真实字段，到时记录到 execution log。
3. **Codex `Stop` 触发频率**：根据 context7 的事件命名（`turn/completed`），`Stop` 可能 per-turn 而非 per-session 触发。这意味着一个 Codex 会话可能写出 10+ 个 .md 文件。Plan 1 的 CLI 已经把 slug 设计成 `<date>-<session_id>`——如果同 session_id 的多次 Stop 都写入，**会互相覆盖**（save_session 用 `path.write_text` 直接重写）。本 plan **不**改这个行为；用户实测如果觉得"只有最后一轮被记下"不合预期，记入 execution log，留到 Plan 3 治理时讨论是否改 slug 加 turn 序号或合并多 turn 内容。
4. **OpenClaw 插件构建产物**：OpenClaw 文档没明示插件是否要预构建（`dist/index.js`）还是 OpenClaw 运行时直接吃 `.ts`。本 plan **采用最小假设**：写一个零依赖的 plain ESM `index.mjs`（避免 TS 编译链），如果 OpenClaw 必须要 TS / `.js`，Task 3 的 subagent 调整成 `tsc` 或重命名。
5. **OpenClaw 插件的 conversation payload 字段**：context7 `docs/plugins/hooks.md` 没具体说 `agent_end` handler 收到什么。本 plan **假设** SDK 会传一个含 `messages: [{role, content}, ...]`、`cwd`、`sessionId` 的对象；Task 3 的插件代码对 unknown 字段宽容。e2e Task 6 实测后记录。
6. **OpenClaw 是否真正使用 Claude Code 作为 backend agent**：4 月研究笔记说 OpenClaw "包在 Claude Code 外面"；但 context7 的 OpenClaw 文档定位是 "multi-platform AI agent gateway"，不一定走 CC。如果用户的 OpenClaw 实际上用 Codex 或别的 backend，三端共享的 e2e 仍然有效（因为我们的 capture 是在 OpenClaw 进程内插的，不依赖 backend），但要在 README 里说清。Task 7 README 写明"无论 OpenClaw backend 是什么 agent，本插件只捕获 OpenClaw 视角的对话"。

如果上面任何一条在实施时被证伪，**修 plan 再继续**，不要硬推。修 plan 走"先更新 spec / 再更新 plan"的硬约束。

---

### Task 1：CLI 加 `--source` 旗标

**Files:**
- Modify: `memoryd/src/memoryd/cli.py:950-1026`
- Modify: `memoryd/tests/test_cli.py` （加 3 个测试，原有 5 个保留）

Plan 1 的 `capture_session` 把 `source` 硬编码成 `"claude-code"`。本 task 加一个可选 `source` 参数（CLI flag `--source`，默认仍 `claude-code`），让 Codex / OpenClaw 的 hook 能传不同值。

- [ ] **Step 1：写失败测试**

在 `memoryd/tests/test_cli.py` 末尾追加（不要删现有 5 个测试；不要重复 import——文件顶部已经有 `from memoryd.cli import capture_session` / `from memoryd.scope import scope_hash` / `from memoryd.storage import list_sessions`）。`load_session` 是新引入需要的 import——在文件顶部 `from memoryd.storage import list_sessions` 改成 `from memoryd.storage import list_sessions, load_session`：

```python
def test_capture_respects_source_param(memory_root: Path, tmp_path: Path):
    """capture_session honors an explicit source value."""
    transcript = tmp_path / "transcript.jsonl"
    _write_fake_transcript(transcript)
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "src-test-1",
        "transcript_path": str(transcript),
        "cwd": str(cwd),
    }

    capture_session(payload, memory_root=memory_root, now=datetime(2026, 5, 13, 10, 0), source="codex")
    sh = scope_hash(cwd)
    files = list_sessions(memory_root, scope_hash=sh)
    assert len(files) == 1
    sess = load_session(files[0])
    assert sess.frontmatter.source == "codex"


def test_capture_defaults_source_to_claude_code(memory_root: Path, tmp_path: Path):
    """No source argument → default 'claude-code' for backward compat."""
    transcript = tmp_path / "transcript.jsonl"
    _write_fake_transcript(transcript)
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "default-src-1",
        "transcript_path": str(transcript),
        "cwd": str(cwd),
    }

    capture_session(payload, memory_root=memory_root, now=datetime(2026, 5, 13, 10, 1))
    sh = scope_hash(cwd)
    files = list_sessions(memory_root, scope_hash=sh)
    sess = load_session(files[0])
    assert sess.frontmatter.source == "claude-code"


def test_main_passes_source_flag_to_capture(memory_root: Path, tmp_path: Path):
    """`memoryd capture --source openclaw` reaches capture_session."""
    transcript = tmp_path / "transcript.jsonl"
    _write_fake_transcript(transcript)
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "stdin-source-test",
        "transcript_path": str(transcript),
        "cwd": str(cwd),
    }

    proc = subprocess.run(
        ["uv", "run", "memoryd", "capture", "--source", "openclaw"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd="/Users/abble/project-management-personal/memoryd",
        env={**os.environ, "MEMORYD_DATA_ROOT": str(memory_root)},
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    sh = scope_hash(cwd)
    files = list_sessions(memory_root, scope_hash=sh)
    assert len(files) == 1
    sess = load_session(files[0])
    assert sess.frontmatter.source == "openclaw"
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_cli.py -v -k "source"`
Expected: 3 FAIL（`capture_session() got unexpected keyword argument 'source'` 或类似）。

- [ ] **Step 3：改 `memoryd/src/memoryd/cli.py`（三处小修，保留 plan 1 sanitization 内联）**

**改动 1：** `capture_session` 函数签名加一个 `source` keyword 参数（默认 `"claude-code"`）。Edit `memoryd/src/memoryd/cli.py:73-78` from：

```python
def capture_session(
    payload: dict[str, Any],
    *,
    memory_root: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Convert a SessionEnd hook payload into a SessionMemory markdown file."""
```

to：

```python
def capture_session(
    payload: dict[str, Any],
    *,
    memory_root: Path | None = None,
    now: datetime | None = None,
    source: str = "claude-code",
) -> Path:
    """Convert a SessionEnd hook payload into a SessionMemory markdown file.

    `source` is recorded in frontmatter for downstream filtering. Conventional
    values: claude-code | codex | openclaw | manual.
    """
```

**改动 2：** Frontmatter 的硬编码 `source="claude-code"` 改成参数。Edit `memoryd/src/memoryd/cli.py:118`（当前 `source="claude-code"`）to `source=source`. The surrounding Frontmatter call stays unchanged—**leave** the inline `session_id = re.sub(...)` sanitization (lines 88-90), `slug` and `title` construction (108-109), and every other line as-is.

**改动 3：** `cmd_capture` 把 `--source` 透传给 `capture_session`。Edit `memoryd/src/memoryd/cli.py:139` from:

```python
    path = capture_session(payload)
```

to:

```python
    path = capture_session(payload, source=args.source)
```

**改动 4：** `main` 的 capture subparser 加 `--source` flag. Edit `memoryd/src/memoryd/cli.py:148-149` from:

```python
    p_capture = subs.add_parser("capture", help="read SessionEnd payload from stdin and save")
    p_capture.set_defaults(func=cmd_capture)
```

to:

```python
    p_capture = subs.add_parser("capture", help="read SessionEnd payload from stdin and save")
    p_capture.add_argument(
        "--source",
        default="claude-code",
        help="origin tool tag written to frontmatter (claude-code | codex | openclaw | ...)",
    )
    p_capture.set_defaults(func=cmd_capture)
```

**Do not** otherwise refactor the file. The inline `re.sub` sanitization, the `_data_root()` / `_read_transcript_text()` / `_summarize_naively()` helpers stay untouched.

- [ ] **Step 4：跑全量测试**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest -v`
Expected: **34 passed**（原 31 + 新 3）。所有 plan 1 测试保持绿。

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/cli.py memoryd/tests/test_cli.py
git commit -m "$(cat <<'EOF'
add --source flag to memoryd capture CLI

Lets Codex / OpenClaw hooks tag their origin in frontmatter; defaults
to claude-code so plan 1 hook keeps working unchanged.
EOF
)"
```

---

### Task 2：Codex Stop hook 脚本

**Files:**
- Create: `scripts/codex-stop-hook.sh`

bash 脚本：从 stdin 拿 Codex `Stop` 事件 payload（假设含 `session_id` / `transcript_path` / `cwd`，CLI 已经容错 missing keys）；管道给 `memoryd capture --source codex`；后台跑不阻塞。整体复用 plan 1 `cc-session-end-hook.sh` 的成熟模式，差异只在 `--source codex`。

- [ ] **Step 1：写脚本 `scripts/codex-stop-hook.sh`**

```bash
#!/usr/bin/env bash
# Codex Stop hook → memoryd capture (source=codex)
#
# Reads JSON payload from stdin (event passed by Codex hooks engine),
# pipes it to `memoryd capture --source codex`. Runs in background so
# Codex's turn-end isn't blocked.
#
# Install: see memoryd/README.md (Codex section).

set -euo pipefail

MEMORYD_BIN="${MEMORYD_BIN:-/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd}"

if [[ ! -x "$MEMORYD_BIN" ]]; then
    echo "[$(date -Iseconds)] codex-stop-hook: memoryd binary not executable at $MEMORYD_BIN" \
        >> "${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs/codex-stop.log" 2>/dev/null || true
    exit 0  # never block Codex's turn-end
fi

PAYLOAD="$(cat)"

LOG_DIR="${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/codex-stop.log"

(
    if printf '%s' "$PAYLOAD" | "$MEMORYD_BIN" capture --source codex >> "$LOG_FILE" 2>&1; then
        printf '%s  ok\n' "$(date -Iseconds)" >> "$LOG_FILE"
    else
        printf '%s  failed (exit %s)\n' "$(date -Iseconds)" "$?" >> "$LOG_FILE"
    fi
) &

disown $! 2>/dev/null || true
exit 0
```

注意：用 `printf '%s'` 而不是 `echo`（plan 1 commit `de1510e` 已经踩过 `echo` 解析 `-n` / `-e` flag 的坑）。

- [ ] **Step 2：让脚本可执行**

Run: `chmod +x /Users/abble/project-management-personal/scripts/codex-stop-hook.sh`
Expected: 无输出。

- [ ] **Step 3：手工烟雾测试**

```bash
cd /Users/abble/project-management-personal
TMPDIR=$(mktemp -d)
TRANSCRIPT="$TMPDIR/transcript.jsonl"
cat > "$TRANSCRIPT" <<'EOF'
{"type":"user","message":{"content":[{"type":"text","text":"codex smoke test query"}]}}
{"type":"assistant","message":{"content":[{"type":"text","text":"codex smoke test response"}]}}
EOF

PAYLOAD=$(cat <<EOF
{"session_id": "codex-smoke-$(date +%s)", "transcript_path": "$TRANSCRIPT", "cwd": "$TMPDIR"}
EOF
)

export PATH="$PWD/memoryd/.venv/bin:$PATH"
export MEMORYD_DATA_ROOT="$TMPDIR/memoryd-data"

printf '%s' "$PAYLOAD" | scripts/codex-stop-hook.sh

sleep 1

# 应该有一个含 source: codex 的 markdown
find "$MEMORYD_DATA_ROOT" -name "*.md" -print
cat "$MEMORYD_DATA_ROOT"/scopes/*/sessions/*.md | head -20
```

Expected:
- `find` 输出 1 个 `.md` 文件
- `cat` 头部 frontmatter 包含 `source: codex`
- `cat $TMPDIR/memoryd-data/logs/codex-stop.log` 包含 `captured ->` 和 `ok`

- [ ] **Step 4：Commit**

```bash
cd /Users/abble/project-management-personal
git add scripts/codex-stop-hook.sh
git commit -m "add Codex Stop hook script (source=codex)"
```

---

### Task 3：OpenClaw 插件包（TypeScript / ESM）

**Files:**
- Create: `scripts/openclaw-memoryd-plugin/package.json`
- Create: `scripts/openclaw-memoryd-plugin/src/index.mjs`
- Create: `scripts/openclaw-memoryd-plugin/tests/index.test.mjs`
- Create: `scripts/openclaw-memoryd-plugin/README.md`

写一个最小 OpenClaw 插件：注册 `agent_end` lifecycle hook，把 turn 数据序列化成 JSON，子进程 spawn `memoryd capture --source openclaw`。**用 plain ESM `.mjs` 避免引 TS 编译链**（见 plan 顶部"风险 4"——如果 OpenClaw 必须要 TS 文件，本 task 跑到 install 步骤会失败，subagent 应停下问用户当前 OpenClaw 版本对插件文件类型的要求）。

- [ ] **Step 1：写 `scripts/openclaw-memoryd-plugin/package.json`**

```json
{
  "name": "@memoryd/openclaw-plugin",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "description": "OpenClaw plugin that captures agent_end sessions into memoryd (source=openclaw).",
  "main": "src/index.mjs",
  "exports": {
    ".": "./src/index.mjs"
  },
  "scripts": {
    "test": "node --test tests"
  },
  "engines": {
    "node": ">=18"
  }
}
```

- [ ] **Step 2：写插件入口 `scripts/openclaw-memoryd-plugin/src/index.mjs`**

```javascript
/**
 * OpenClaw plugin: capture agent_end turns into memoryd.
 *
 * Registers on `agent_end` lifecycle hook. Receives a turn record from
 * OpenClaw's SDK and converts it into the same JSON payload shape that
 * `memoryd capture` accepts on stdin (session_id / transcript_path / cwd),
 * then spawns `memoryd capture --source openclaw`.
 *
 * Because memoryd's CLI reads transcript content from a JSONL file
 * (see _read_transcript_text in memoryd/src/memoryd/cli.py), this plugin
 * materializes OpenClaw's inline messages into a tmp JSONL in the same
 * format Claude Code emits ({"type":"user|assistant","message":{"content":
 * [{"type":"text","text":...}]}}). That way Plan 1's existing parser
 * sees a normal transcript and writes a real summary, not a stub.
 *
 * Requires `plugins.entries.<this-plugin>.hooks.allowConversationAccess = true`
 * for OpenClaw's SDK to actually deliver message content. Without it,
 * `event.messages` will be empty/redacted; we fall back to a stub session
 * tagged source=openclaw so the data root still records that OpenClaw was active.
 */

import { spawn } from "node:child_process";
import { mkdirSync, appendFileSync, existsSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

const DEFAULT_MEMORYD_BIN =
  "/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd";

function logFor() {
  const dataRoot =
    process.env.MEMORYD_DATA_ROOT || join(homedir(), ".local", "share", "memoryd");
  const logDir = join(dataRoot, "logs");
  if (!existsSync(logDir)) mkdirSync(logDir, { recursive: true });
  return join(logDir, "openclaw-agent-end.log");
}

function tsLine(extra) {
  return `${new Date().toISOString()}  ${extra}\n`;
}

/**
 * Normalize one OpenClaw message into the CC transcript JSONL shape.
 * OpenClaw's payload schema isn't fully documented; we accept a few
 * shapes and bail to null if we can't extract text.
 */
function normalizeMessage(m) {
  if (!m || typeof m !== "object") return null;
  const role = m.role || m.author || m.from || "user";
  const type = role === "assistant" || role === "agent" ? "assistant" : "user";

  let text = null;
  if (typeof m.content === "string") {
    text = m.content;
  } else if (Array.isArray(m.content)) {
    text = m.content
      .map((c) => (typeof c === "string" ? c : c?.text || c?.value || ""))
      .filter(Boolean)
      .join("\n");
  } else if (typeof m.text === "string") {
    text = m.text;
  }
  if (!text) return null;

  return {
    type,
    message: { content: [{ type: "text", text }] },
  };
}

/**
 * If `event.messages` (or .turns, .conversation) has inline content,
 * write a tmp JSONL in CC's transcript format and return its path.
 * Otherwise return "" — the CLI will write a stub session.
 */
export function materializeTranscript(event, { tmpDir = tmpdir() } = {}) {
  const raw = event?.transcriptPath || event?.transcript_path;
  if (typeof raw === "string" && raw.length > 0) return raw;

  const messages =
    event?.messages || event?.turns || event?.conversation || event?.history || null;
  if (!Array.isArray(messages) || messages.length === 0) return "";

  const normalized = messages.map(normalizeMessage).filter(Boolean);
  if (normalized.length === 0) return "";

  const sid = event?.sessionId || event?.session_id || "openclaw";
  const safeSid = String(sid).replace(/[^A-Za-z0-9_-]/g, "_");
  const path = join(tmpDir, `openclaw-${safeSid}-${Date.now()}.jsonl`);
  writeFileSync(path, normalized.map((l) => JSON.stringify(l)).join("\n"));
  return path;
}

/**
 * Translate an OpenClaw agent_end event into the JSON payload that
 * `memoryd capture` reads on stdin. Tolerant of missing fields—the
 * CLI handles partial payloads (plan 1 behavior).
 */
export function buildPayload(event, opts) {
  const sessionId =
    event?.sessionId || event?.session_id || event?.threadId || "openclaw-unknown";
  const cwd = event?.cwd || event?.workspace?.cwd || process.cwd();
  const transcriptPath = materializeTranscript(event, opts);

  return {
    session_id: sessionId,
    transcript_path: transcriptPath,
    cwd,
  };
}

export function spawnCapture(payload, { bin = DEFAULT_MEMORYD_BIN, logFile } = {}) {
  const logPath = logFile || logFor();
  const child = spawn(bin, ["capture", "--source", "openclaw"], {
    stdio: ["pipe", "pipe", "pipe"],
    detached: true,
  });
  child.stdout.on("data", (chunk) => appendFileSync(logPath, chunk));
  child.stderr.on("data", (chunk) => appendFileSync(logPath, chunk));
  child.on("close", (code) => {
    appendFileSync(logPath, tsLine(code === 0 ? "ok" : `failed (exit ${code})`));
  });
  child.on("error", (err) => {
    appendFileSync(logPath, tsLine(`spawn error: ${err.message}`));
  });
  child.stdin.write(JSON.stringify(payload));
  child.stdin.end();
  child.unref();
  return child;
}

/**
 * OpenClaw plugin entry. The SDK injects `api`; we call `api.on('agent_end', ...)`.
 * See https://github.com/openclaw/openclaw/blob/main/docs/plugins/sdk-overview.md
 */
export default function register(api) {
  api.on("agent_end", async (event) => {
    try {
      const payload = buildPayload(event);
      spawnCapture(payload);
    } catch (err) {
      // Never let plugin errors crash OpenClaw.
      try {
        appendFileSync(logFor(), tsLine(`handler error: ${err.message}`));
      } catch (_) {
        // give up silently
      }
    }
  });
}
```

- [ ] **Step 3：写最小单测 `scripts/openclaw-memoryd-plugin/tests/index.test.mjs`**

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildPayload, materializeTranscript } from "../src/index.mjs";

test("buildPayload extracts canonical fields when transcriptPath given", () => {
  const ev = {
    sessionId: "ow-123",
    cwd: "/Users/abble/projects/wolin",
    transcriptPath: "/tmp/ow.jsonl",
  };
  const p = buildPayload(ev);
  assert.equal(p.session_id, "ow-123");
  assert.equal(p.cwd, "/Users/abble/projects/wolin");
  assert.equal(p.transcript_path, "/tmp/ow.jsonl");
});

test("buildPayload falls back to snake_case keys", () => {
  const ev = {
    session_id: "ow-snake",
    workspace: { cwd: "/tmp/snake-proj" },
    transcript_path: "",
  };
  const p = buildPayload(ev);
  assert.equal(p.session_id, "ow-snake");
  assert.equal(p.cwd, "/tmp/snake-proj");
  assert.equal(p.transcript_path, "");
});

test("buildPayload returns empty transcript_path on missing fields", () => {
  const p = buildPayload({});
  assert.equal(p.session_id, "openclaw-unknown");
  assert.ok(typeof p.cwd === "string");
  assert.equal(p.transcript_path, "");
});

test("buildPayload tolerates null event", () => {
  const p = buildPayload(null);
  assert.equal(p.session_id, "openclaw-unknown");
});

test("materializeTranscript writes inline messages as CC-format JSONL", () => {
  const dir = mkdtempSync(join(tmpdir(), "ow-test-"));
  const ev = {
    sessionId: "ow-inline",
    messages: [
      { role: "user", content: "你好" },
      { role: "assistant", content: [{ type: "text", text: "hi back" }] },
      { author: "user", text: "再问一句" },
    ],
  };
  const path = materializeTranscript(ev, { tmpDir: dir });
  assert.ok(existsSync(path));
  const lines = readFileSync(path, "utf-8").trim().split("\n");
  assert.equal(lines.length, 3);
  const first = JSON.parse(lines[0]);
  assert.equal(first.type, "user");
  assert.equal(first.message.content[0].text, "你好");
  const second = JSON.parse(lines[1]);
  assert.equal(second.type, "assistant");
  assert.equal(second.message.content[0].text, "hi back");
  const third = JSON.parse(lines[2]);
  assert.equal(third.type, "user");
  assert.equal(third.message.content[0].text, "再问一句");
});

test("materializeTranscript returns empty string when no messages and no path", () => {
  const dir = mkdtempSync(join(tmpdir(), "ow-test-"));
  assert.equal(materializeTranscript({}, { tmpDir: dir }), "");
  assert.equal(materializeTranscript({ messages: [] }, { tmpDir: dir }), "");
  assert.equal(materializeTranscript(null, { tmpDir: dir }), "");
});

test("materializeTranscript prefers transcriptPath over inline messages", () => {
  const dir = mkdtempSync(join(tmpdir(), "ow-test-"));
  const ev = {
    transcriptPath: "/preset/path.jsonl",
    messages: [{ role: "user", content: "x" }],
  };
  assert.equal(materializeTranscript(ev, { tmpDir: dir }), "/preset/path.jsonl");
});
```

- [ ] **Step 4：写 `scripts/openclaw-memoryd-plugin/README.md`**

```markdown
# memoryd OpenClaw plugin

Captures every `agent_end` turn into the local memoryd data root, tagged
`source: openclaw`. Co-installed alongside the CC SessionEnd hook + Codex
Stop hook so all three clients share a single Markdown scope.

## Install (one-time)

```bash
cd /path/to/project-management-personal/scripts/openclaw-memoryd-plugin
openclaw plugins install --force .
```

Grant the two hook permissions OpenClaw requires:

```bash
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowConversationAccess true
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowPromptInjection false
```

(We only need conversation read access; we do NOT inject prompts.)

## Verify

After your next OpenClaw turn ends, check:

```bash
ls ~/.local/share/memoryd/scopes/*/sessions/
cat ~/.local/share/memoryd/logs/openclaw-agent-end.log
```

The log should show `ok`; the markdown's frontmatter should include
`source: openclaw`.

## Run tests

```bash
npm test
```
```

- [ ] **Step 5：跑单测**

Run: `cd /Users/abble/project-management-personal/scripts/openclaw-memoryd-plugin && node --test tests`
Expected: 7 passed (4 `buildPayload` + 3 `materializeTranscript`).

如果 Node 版本 < 18 报错，subagent 用 `node --version` 检查后**停下问用户**怎么处理（升级 Node vs 把测试改成 commonjs assert）。

- [ ] **Step 6：Commit**

```bash
cd /Users/abble/project-management-personal
git add scripts/openclaw-memoryd-plugin/
git commit -m "$(cat <<'EOF'
add OpenClaw plugin (agent_end → memoryd capture --source openclaw)

Plain ESM, zero npm deps, spawns memoryd binary like the CC + Codex
hooks. Permission grant instructions in plugin README.
EOF
)"
```

---

### Task 4：把 Codex hook 接到用户 Codex 配置

**Files:**
- Modify: `~/.codex/hooks.json` （用户配置——不在 repo；用 Python json read-mutate-write，绝不覆盖）
- Possibly modify: `~/.codex/config.toml` （仅当探测发现需要启用 hooks feature flag 时）

**重要：** 这一步改用户全局 Codex 配置。**先 backup**，再用 Python json 模块合并（与 plan 1 wire-up 同手法）。**绝不**用 `jq`、`sed`、shell 重定向覆盖。

- [ ] **Step 1：备份用户当前 Codex 配置**

```bash
mkdir -p ~/.claude/backups  # 复用 plan 1 的备份目录
cp ~/.codex/hooks.json ~/.claude/backups/codex.hooks.json.bak.$(date +%Y%m%d-%H%M%S) 2>/dev/null || echo "no existing ~/.codex/hooks.json"
cp ~/.codex/config.toml ~/.claude/backups/codex.config.toml.bak.$(date +%Y%m%d-%H%M%S) 2>/dev/null || echo "no existing ~/.codex/config.toml"
```

- [ ] **Step 2：探测 Codex 当前对 hooks 的支持**

```bash
# 看 Codex 版本
codex --version 2>&1 || echo "codex CLI not installed; STOP and ask user"

# 看 hooks 是否需要 feature flag（context7 表明 hooks.json 直接被识别；但 memsearch 文档曾提"启用 feature flag"——以本机现实为准）
test -f ~/.codex/hooks.json && echo "hooks.json present" || echo "hooks.json absent (will create)"
test -f ~/.codex/config.toml && head -30 ~/.codex/config.toml 2>/dev/null || echo "config.toml absent"
```

如果 `codex --version` 报"command not found"，**停下问用户** Codex 是否已装、安装路径——后续 e2e（Task 6）需要 Codex 可用。

如果 `config.toml` 里有看起来禁用 hooks 的 flag（比如 `[experimental] hooks = false`），**停下问用户**是否启用——本 plan 不擅自改实验功能开关。

- [ ] **Step 3：合并 hook 条目进 `~/.codex/hooks.json`**

用 Python read-mutate-write（绝不直接覆盖；现有 `hooks.json` 可能有其他事件 / handler）：

```bash
python3 <<'PY'
import json
from pathlib import Path

path = Path.home() / ".codex" / "hooks.json"
path.parent.mkdir(parents=True, exist_ok=True)

if path.exists():
    with open(path) as f:
        d = json.load(f)
else:
    d = {}

d.setdefault("hooks", {})
d["hooks"].setdefault("Stop", [])

# 检查是否已经有指向 codex-stop-hook.sh 的条目；幂等
HOOK_CMD = "/Users/abble/project-management-personal/scripts/codex-stop-hook.sh"
already = any(
    h.get("command") == HOOK_CMD
    for group in d["hooks"].get("Stop", [])
    for h in group.get("hooks", [])
)

if not already:
    d["hooks"]["Stop"].append({
        "hooks": [{
            "type": "command",
            "command": HOOK_CMD,
            "async": True,
            "statusMessage": "memoryd capture (codex)"
        }]
    })

tmp = path.with_suffix(".json.tmp")
with open(tmp, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
tmp.replace(path)
print("merged ok; Stop hooks count:", len(d["hooks"]["Stop"]))
PY
```

确认结果：

```bash
python3 -c "import json; d = json.load(open('/Users/abble/.codex/hooks.json')); print(json.dumps(d, indent=2))"
```

Expected: 输出含 `Stop` 数组，其中含一项 `command: /Users/abble/project-management-personal/scripts/codex-stop-hook.sh`，`async: true`。原有其他 hooks（若有）原封不动。

- [ ] **Step 4：日志记录**

```bash
echo "[$(date -Iseconds)] wired codex Stop hook -> codex-stop-hook.sh in ~/.codex/hooks.json" \
    >> /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt
```

- [ ] **Step 5：本步骤无 commit**（改的是用户配置）

---

### Task 5：把 OpenClaw 插件接到用户 OpenClaw 实例

**Files:**
- 用户 OpenClaw 全局配置（通过 `openclaw config` CLI 自管）

**前置：** Task 3 的插件已写好；Task 4 已结束。

- [ ] **Step 1：探测 OpenClaw 可用性**

```bash
openclaw --version 2>&1 || echo "openclaw CLI not installed; STOP and ask user"
```

如果未装 OpenClaw，**停下问用户**是否准备好实例 / 是否要把 Task 5 暂搁直到用户装好——本 plan 后续 e2e Task 6 第三步需要 OpenClaw 可用。

- [ ] **Step 2：安装插件（本地路径模式）**

```bash
cd /Users/abble/project-management-personal/scripts/openclaw-memoryd-plugin
openclaw plugins install --force .
```

Expected: 输出表明插件已安装；OpenClaw config 里出现 `plugins.entries.memoryd-openclaw`（或插件名规范化后的 key——取决于 OpenClaw 对 `name` 字段的归一化）。**记录** OpenClaw 实际给的 entry key 到 execution log——Step 3 要用。

- [ ] **Step 3：授权 `allowConversationAccess`**

```bash
# 用 Step 2 记录的实际 entry key 替换 <ENTRY_KEY>
openclaw config set plugins.entries.<ENTRY_KEY>.hooks.allowConversationAccess true
openclaw config set plugins.entries.<ENTRY_KEY>.hooks.allowPromptInjection false
openclaw config get plugins.entries.<ENTRY_KEY>.hooks
```

Expected: get 输出含 `allowConversationAccess: true`、`allowPromptInjection: false`。

- [ ] **Step 4：日志记录**

```bash
echo "[$(date -Iseconds)] installed openclaw-memoryd-plugin (entry key: <ENTRY_KEY>), granted allowConversationAccess" \
    >> /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt
```

- [ ] **Step 5：本步骤无 commit**

---

### Task 6：端到端跨端召回手测

**Files:** 无 repo 改动。用户在本地跑——subagent 不能交互打开 CC / Codex / OpenClaw。

**前置：** Plan 1 的 CC hook + 本 plan Task 4 的 Codex hook + Task 5 的 OpenClaw 插件**都装好**且 backup 已留。

**Subagent 角色：** 写一份**给用户的 e2e 操作手册**到 execution log，并在用户回执测试结果后记录 PASS / FAIL。subagent 自己不跑 `claude` / `codex` / openclaw 命令——那是交互式 TUI。

- [ ] **Step 1：subagent 写 e2e 手册片段并 push 给用户**

把以下 Markdown 写到 `docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt`（追加在已有日志后），然后让用户在终端里跑：

```text
=== Tri-client e2e (user manual run) ===

# 0. 准备纯净 scope
TEST_PROJECT="$HOME/tmp/memoryd-tri-e2e-$(date +%s)"
mkdir -p "$TEST_PROJECT"
cd "$TEST_PROJECT"
git init -q

SCOPE_HASH=$(python3 -c "
import sys
sys.path.insert(0, '/Users/abble/project-management-personal/memoryd/src')
from memoryd.scope import scope_hash, resolve_scope_root
from pathlib import Path
print(scope_hash(resolve_scope_root(Path('$TEST_PROJECT'))))
")
echo "scope_hash: $SCOPE_HASH"

# 1. CC 写第一条 (token CC-WATERMELON-7777)
cd "$TEST_PROJECT"
claude
> 我在 tri-client-e2e 测试 memoryd。请记住：暗号 CC-WATERMELON-7777。
> /exit
sleep 2
ls "$HOME/.local/share/memoryd/scopes/$SCOPE_HASH/sessions/" | grep -i "$(date +%Y-%m-%d)" \
  | xargs -I{} grep -l "claude-code" "$HOME/.local/share/memoryd/scopes/$SCOPE_HASH/sessions/{}"

# 2. Codex 写第二条 (token CODEX-PEAR-3333)
cd "$TEST_PROJECT"
codex
> 我在同一目录里。请记住：暗号 CODEX-PEAR-3333。
> /exit
sleep 2
grep -rl "source: codex" "$HOME/.local/share/memoryd/scopes/$SCOPE_HASH/sessions/"

# 3. OpenClaw 写第三条 (token OPENCLAW-MANGO-4242)
cd "$TEST_PROJECT"
openclaw  # 或对应启动命令
> 我在同一目录里。请记住：暗号 OPENCLAW-MANGO-4242。
> 退出 / 结束 turn
sleep 2
grep -rl "source: openclaw" "$HOME/.local/share/memoryd/scopes/$SCOPE_HASH/sessions/"

# 4. 三端互相召回验证
# 4a. CC 召回 CODEX 和 OPENCLAW 的暗号
cd "$TEST_PROJECT"
claude
> 用 search_memory 工具找一下"PEAR"和"MANGO"的暗号。

# 期望：智能体应当调用 memoryd search_memory，找到 CODEX-PEAR-3333 和 OPENCLAW-MANGO-4242

# 4b. Codex 召回 CC 和 OPENCLAW 的暗号
codex
> 用 memoryd 搜一下"WATERMELON"和"MANGO"。
# 期望：找到两个暗号

# 4c. OpenClaw 召回 CC 和 CODEX 的暗号
openclaw
> 用 memoryd 找"WATERMELON"和"PEAR"。
# 期望：找到两个暗号

=== e2e end ===
```

- [ ] **Step 2：用户回执后 subagent 记录结果**

用户回 PASS / FAIL；FAIL 时记录失败现象（哪一步 grep 没找到、哪一端没召回到对面）：

```bash
# 用户回 PASS：
echo "[$(date -Iseconds)] tri-client e2e PASS: CC↔Codex↔OpenClaw all recall each other in scope $SCOPE_HASH" \
    >> /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt

# 用户回 FAIL：
echo "[$(date -Iseconds)] tri-client e2e FAIL: <症状描述>" \
    >> /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt
```

如果 FAIL，**停下问用户**症状细节后再排错；不要硬推到 Task 7。

- [ ] **Step 3：commit execution log**

```bash
cd /Users/abble/project-management-personal
git add docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt
git commit -m "log tri-client e2e result"
```

---

### Task 7：README 补 Codex / OpenClaw wire-up 章节

**Files:**
- Modify: `memoryd/README.md`

把 Codex 和 OpenClaw 的 wire-up 写到现有 README"Wire into Claude Code"章节后面（不要替换 CC 章节——CC wire-up 沿用 plan 1）。

- [ ] **Step 1：在 `memoryd/README.md` 现有"Wire into Claude Code"小节后面追加**

```markdown
## Wire into Codex

> Codex doesn't have a `SessionEnd` event; we hook the `Stop` event, which
> fires when a turn finishes. Multiple turns in one Codex session will
> overwrite the same `<date>-<session_id>.md` file — the last turn's
> summary wins. Long-term governance (incl. per-turn slugs or merged
> summaries) lands in plan 3.

1. Backup your current `~/.codex/hooks.json`:
   ```bash
   mkdir -p ~/.claude/backups
   cp ~/.codex/hooks.json ~/.claude/backups/codex.hooks.json.bak.$(date +%Y%m%d-%H%M%S) 2>/dev/null || echo "no existing hooks.json"
   ```

2. Merge the Stop hook into `~/.codex/hooks.json` (use Python so other hooks survive):
   ```python
   import json
   from pathlib import Path
   p = Path.home() / ".codex" / "hooks.json"
   p.parent.mkdir(parents=True, exist_ok=True)
   d = json.loads(p.read_text()) if p.exists() else {}
   d.setdefault("hooks", {}).setdefault("Stop", []).append({
       "hooks": [{
           "type": "command",
           "command": "/path/to/project-management-personal/scripts/codex-stop-hook.sh",
           "async": True,
           "statusMessage": "memoryd capture (codex)"
       }]
   })
   tmp = p.with_suffix(".json.tmp")
   tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False))
   tmp.replace(p)
   ```

3. Restart Codex. Run any turn; check `~/.local/share/memoryd/logs/codex-stop.log` for `ok`.

## Wire into OpenClaw

> The OpenClaw plugin lives under `scripts/openclaw-memoryd-plugin/`. It
> registers on the `agent_end` lifecycle hook and requires the
> `allowConversationAccess` permission to read turn data.

1. Install the plugin:
   ```bash
   cd /path/to/project-management-personal/scripts/openclaw-memoryd-plugin
   openclaw plugins install --force .
   ```

2. Grant conversation read permission (the plugin does NOT inject prompts):
   ```bash
   # Replace <ENTRY_KEY> with what `openclaw plugins install` printed
   openclaw config set plugins.entries.<ENTRY_KEY>.hooks.allowConversationAccess true
   openclaw config set plugins.entries.<ENTRY_KEY>.hooks.allowPromptInjection false
   ```

3. Run any OpenClaw turn; check `~/.local/share/memoryd/logs/openclaw-agent-end.log` for `ok`.

**Note on OpenClaw backend agent:** Whatever backend agent OpenClaw routes
your messages to (Claude Code, GPT, etc.), this plugin captures the turn
from OpenClaw's view — so memories written via OpenClaw appear with
`source: openclaw`, distinct from the same backend's native source tag.
```

也在文件顶部"Currently supports"清单**只更新一行**：

```markdown
- macOS only
- **Claude Code, Codex, and OpenClaw three clients share a single scope** (was: Claude Code only)
- Single machine (multi-machine sync in plan 6)
...
```

- [ ] **Step 2：跑全量测试 + 单测确认无回归**

```bash
cd /Users/abble/project-management-personal/memoryd && uv run pytest -v
cd /Users/abble/project-management-personal/scripts/openclaw-memoryd-plugin && node --test tests
```
Expected: pytest 34 passed；node test 7 passed。

- [ ] **Step 3：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/README.md
git commit -m "expand memoryd README with Codex + OpenClaw wire-up"
```

- [ ] **Step 4：标记 plan 2 完成**

```bash
echo "[$(date -Iseconds)] plan 2 (tri-client session capture) implementation tasks complete; e2e $(grep -c 'PASS' docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt 2>/dev/null || echo 0) pass(es) recorded" \
    >> /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt
git add docs/superpowers/plans/2026-05-13-tri-client-session-capture.execution-log.txt
git commit -m "log plan 2 implementation completion"
```

---

## Plan 2 完成判定

下面任一条**未达成**即认为 plan 2 未完成：

1. ✅ memoryd 测试 34 passed（plan 1 的 31 + 本 plan 加的 3）
2. ✅ OpenClaw 插件 7 个 node test 全 pass（4 个 buildPayload + 3 个 materializeTranscript）
3. ✅ Task 6 e2e：同一目录下三端各写一条暗号，每一端的新会话能召回另两端的暗号（共 6 个跨端召回成功）
4. ✅ `~/.codex/hooks.json` 合并后原有 hooks（若有）完整无损；OpenClaw config 中原有插件 entries 完整无损
5. ✅ 至少 3 个 `.md` session 文件分别带 `source: claude-code` / `source: codex` / `source: openclaw`
6. ✅ 三端 hooks 都是后台跑，不阻塞工具退出 / turn 结束

## Plan 间依赖

**上游：** Plan 1（merged on main，commit `2e7fb25`）——本 plan 复用其 schema / scope / storage / search / MCP server / CLI / CC hook。

**下游：**
- **Plan 3（长期记忆治理）**：依赖本 plan 的 `--source` 字段——governance prompt 会根据 source 不同采用不同的提升判据（比如 CC 会话 vs Codex 会话 vs OpenClaw 跨渠道会话的可信度可能要分别打分）。
- **Plan 5（跨平台）**：本 plan 的 Codex hook 是 bash，OpenClaw 插件是 Node——Plan 5 需要给 bash 写 PowerShell / Python 等价物，给 Node 插件保证 Win / Linux Node 18+ 可跑。
- **Plan 8（旧记忆导入）**：本 plan 巩固"source 字段是开放枚举"的约定，Plan 8 导入旧 CLAUDE.md / AGENTS.md 时会再加 `claude-md` / `agents-md` 等 source 值。
