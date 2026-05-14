# tri-client-capture-fix（Plan 2.5）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Plan 2 已 merge 但实际不工作的 capture 通路：Codex 用 notify wrapper（实时）+ FS-watch `~/.codex/memories/rollout_summaries/`（事后）；OpenClaw 用新 SDK（`definePluginEntry` + `registerAgentEventSubscription`）+ FS-watch `~/.openclaw/agents/*/sessions/`。两条 FS-watch 通路合并到单个 launchd 管理的 daemon 进程。

**Architecture:** memoryd 加 `mirror` 子命令运行 watchdog 监听双通道；Codex notify 经 wrapper 脚本透传原 SkyComputerUseClient 调用同时 fork memoryd capture；OpenClaw 插件重写为 `definePluginEntry`，订阅 `lifecycle` 事件流。源标签 5 类（`claude-code` / `codex-notify` / `codex-rollout` / `openclaw` / `openclaw-fs`），不做 cross-path 去重（推迟到 Plan 3 + SQLite）。

**Tech Stack:** Python 3.11+（uv, pytest, `watchdog>=4` 新增依赖）；bash（wrapper / probe 脚本）；Node 18+ ESM（OpenClaw 插件）；`@openclaw/plugin-sdk`（OpenClaw 运行时提供，测试用 mock）；launchd plist（macOS user LaunchAgent）。

**Spec：** `docs/superpowers/specs/2026-05-14-tri-client-capture-fix-design.md`（commit `6796686`）。

**Decomposition Note:** 8 plan 中的 2.5（修复性）；上游 Plan 1（`2e7fb25`）+ Plan 2（`5a61f43`）已 merge，本 plan 仅替换 Plan 2 的 capture 通路实现，不动 schema / storage / search / MCP server。下游 Plan 3 开始上 SQLite 索引和 cross-path merging。

---

## 文件结构

执行后会产生 / 修改这些文件（所有路径都从 repo 根 `/Users/abble/project-management-personal/` 算起）：

| 路径 | 责任 | 操作 |
|---|---|---|
| `memoryd/pyproject.toml` | 加 `watchdog>=4` 运行时依赖 | Modify |
| `memoryd/src/memoryd/mirror.py` | watchdog handler 公共框架 + `_unscoped` 桶 helper | Create |
| `memoryd/src/memoryd/mirror_codex.py` | rollout_summary `.md` → SessionMemory(source=codex-rollout)；handler 子类 | Create |
| `memoryd/src/memoryd/mirror_openclaw.py` | OpenClaw `.jsonl` session → SessionMemory(source=openclaw-fs)；handler 子类 + scope 反推（内容扫描） | Create |
| `memoryd/src/memoryd/cli.py` | 加 `mirror` 子命令 + `setup` 子命令组 | Modify |
| `memoryd/src/memoryd/setup.py` | 用户配置管理（swap notify / remove dead hook / install launchd） | Create |
| `memoryd/tests/test_mirror.py` | `_unscoped` 桶逻辑 + watchdog 启停 smoke test | Create |
| `memoryd/tests/test_mirror_codex.py` | rollout 转码 + handler 单测 | Create |
| `memoryd/tests/test_mirror_openclaw.py` | jsonl 转码 + handler 单测 + scope 内容反推 | Create |
| `memoryd/tests/test_setup.py` | setup 子命令幂等性 + backup + read-mutate-write | Create |
| `scripts/codex-notify-probe.sh` | Phase 1 探针：log argv + stdin + env，no-op 否则 | Create |
| `scripts/codex-notify-wrapper.sh` | 真 wrapper：透传 SkyComputerUseClient + fork memoryd capture | Create |
| `scripts/launchd/com.memoryd.mirror.plist` | LaunchAgent 模板（含 `__PROJECT_ROOT__` 占位符） | Create |
| `scripts/openclaw-memoryd-plugin/openclaw.plugin.json` | SDK manifest（id / kind / contracts / configSchema） | Create |
| `scripts/openclaw-memoryd-plugin/package.json` | `main` 改 src/index.js + 加 `openclaw.extensions` | Modify |
| `scripts/openclaw-memoryd-plugin/src/payload.js` | 从老 index.mjs 抽出 pure helpers | Create |
| `scripts/openclaw-memoryd-plugin/src/register.js` | makeHandler + makeSubscription（不 import SDK，可单测） | Create |
| `scripts/openclaw-memoryd-plugin/src/index.js` | SDK entry：`definePluginEntry` + `registerAgentEventSubscription` | Create |
| `scripts/openclaw-memoryd-plugin/src/index.mjs` | 老入口 | Delete |
| `scripts/openclaw-memoryd-plugin/tests/payload.test.mjs` | 把 buildPayload / materializeTranscript 测试从 index.test.mjs 改名 + import 调整 | Rename + Modify |
| `scripts/openclaw-memoryd-plugin/tests/register.test.mjs` | 新：mock api 验 subscription 注册 + handler 触发 spawnCapture | Create |
| `scripts/openclaw-memoryd-plugin/tests/index.test.mjs` | 老入口测试 | Delete |
| `scripts/codex-stop-hook.sh` | Codex hooks 不 fire，死代码 | Delete |
| `memoryd/README.md` | Codex / OpenClaw 章节按新通路重写 | Modify |
| `docs/superpowers/plans/2026-05-14-tri-client-capture-fix.execution-log.txt` | 实施进度 + Phase 1 用户操作手册 | Create |

**默认运行时数据目录：** `~/.local/share/memoryd/scopes/<scope-hash>/sessions/<YYYY-MM-DD>-<slug>.md`（与 Plan 1 一致；新增 `_unscoped` scope 给反推失败的条目兜底）。

---

## Codex rollout_summary 真实格式（已实测）

样本 `~/.codex/memories/rollout_summaries/2026-05-13T08-56-13-xoIz-*.md` 头部 5 行 plain text key:value（**不是 YAML frontmatter**，没有 `---` 分隔符）：

```
thread_id: 019e208d-06c4-7ab2-905c-1243dd8a2cd3
updated_at: 2026-05-13T12:46:41+00:00
rollout_path: /Users/abble/.codex/sessions/2026/05/13/rollout-2026-05-13T16-56-13-019e208d-06c4-7ab2-905c-1243dd8a2cd3.jsonl
cwd: /Users/abble/Moonlight-Radiance-game
git_branch: codex/0-0-1-task0-preproduction

# <one-line title>

Rollout context: ...

## Task 1: ...
```

**关键：`cwd:` 字段显式给出**——scope 反推直接用这一行，不需要内容扫描。

---

## 风险与不确定性（先读再开工）

1. **OpenClaw `lifecycle` stream 的具体事件类型**：`AgentEventStream = "lifecycle" | "tool" | "assistant" | ... | (string & {})`，但 `AgentEventPayload` 里 `event.data` 的形状要看运行时才能知道（SDK `.d.ts` 没暴露完整 schema）。Task 6 的 handler 策略：订阅 `["lifecycle"]`，把每个事件先 dump 到 `~/.local/share/memoryd/logs/openclaw-events.log`，handler 仅在事件含 `cwd` 或 `messages` 字段时调 `spawnCapture`。Phase 1 用户跑一轮 OpenClaw 后看日志，下一个 session 据此 narrow filter。
2. **`registerAgentEventSubscription` 是否真的在 OpenClaw 2026.5.7 runtime 暴露**：`.d.ts` 在 SDK package 里列了，但 active-memory / memory-core 没用这个 API（它们用 `registerMemoryCapability`）。若运行时实际不调 handle，Path B（FS-watch ~/.openclaw/agents/）兜底。
3. **`notify` field 真实 argv/stdin/env**：Phase 1 第 1 步探针实测。如果 notify 不携带 cwd / session 数据，Path A 退化为"只能记录 turn 发生过、内容空"，本质上和 Path B 重复——这时 Task 5 的 wrapper 仍部署（保 Codex Computer Use 不挂），但实际 capture 由 Path B 承担。
4. **OpenClaw session jsonl 格式**：`~/.openclaw/agents/` 当前在用户机器上可能为空（OpenClaw 刚装）。Task 3 的 transcoder 写成宽容形态：每行 JSON 解析，把任意 `text` / `content` 字段串起来当 body，cwd 通过内容扫描反推。Phase 1 后续如果格式更明确，Plan 3 再 narrow。
5. **launchd PATH 问题**：LaunchAgent 启动的进程默认 PATH 极简，可能找不到 `uv` / `rg`。plist 里显式设 `PATH=/opt/homebrew/bin:/usr/bin:/bin` + 用绝对路径调 `memoryd-server`。

---

## Task 1：scope 反推 & watchdog 框架

**Files:**
- Modify: `memoryd/pyproject.toml`
- Create: `memoryd/src/memoryd/mirror.py`
- Create: `memoryd/tests/test_mirror.py`

加 `watchdog` 依赖；写公共 watchdog handler 基类，封装"新文件落地 → 转码 → save_session"通用流程，以及 `_unscoped` 兜底桶。

- [ ] **Step 1：加 `watchdog` 依赖到 `memoryd/pyproject.toml`**

把现有 `dependencies` 列表（line 7-11 区域）从：

```toml
dependencies = [
    "mcp[cli]>=1.0.0",
    "pydantic>=2.0",
    "pyyaml>=6.0",
]
```

改成：

```toml
dependencies = [
    "mcp[cli]>=1.0.0",
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "watchdog>=4.0",
]
```

跑 `cd /Users/abble/project-management-personal/memoryd && uv sync` 验证安装成功（应输出 `Installed X package(s)` 包含 `watchdog`）。

- [ ] **Step 2：写失败测试 `memoryd/tests/test_mirror.py`**

```python
"""Mirror framework tests."""
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.mirror import (
    MirrorRouter,
    UNSCOPED_HASH,
    save_to_scope_or_unscoped,
)
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import list_sessions


def _build_session(scope_hash: str = "abc123", source: str = "codex-rollout") -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug="2026-05-14-t",
            type="session",
            scope_hash=scope_hash,
            triggers=[],
            source=source,
            created_at=datetime(2026, 5, 14, 10, 0),
        ),
        body="b",
    )


def test_save_with_real_scope_lands_in_scope_dir(memory_root: Path):
    sess = _build_session(scope_hash="real_scope")
    path = save_to_scope_or_unscoped(memory_root, sess, resolved_scope_hash="real_scope")
    assert "real_scope" in str(path)
    assert UNSCOPED_HASH not in str(path)
    assert path.exists()


def test_save_with_none_scope_lands_in_unscoped(memory_root: Path):
    sess = _build_session()
    path = save_to_scope_or_unscoped(memory_root, sess, resolved_scope_hash=None)
    assert UNSCOPED_HASH in str(path)
    # frontmatter scope_hash should be rewritten to UNSCOPED_HASH
    from memoryd.storage import load_session
    loaded = load_session(path)
    assert loaded.frontmatter.scope_hash == UNSCOPED_HASH


def test_router_routes_by_suffix(memory_root: Path, tmp_path: Path):
    """MirrorRouter dispatches new files to a registered handler by suffix."""
    triggered: list[Path] = []

    def fake_handler(path: Path) -> None:
        triggered.append(path)

    router = MirrorRouter()
    router.register(suffix=".md", handler=fake_handler)

    test_file = tmp_path / "x.md"
    test_file.write_text("hello")
    router.dispatch(test_file)
    assert triggered == [test_file]


def test_router_ignores_unknown_suffix(tmp_path: Path):
    router = MirrorRouter()
    router.register(suffix=".md", handler=lambda p: None)
    triggered = []
    router.register(suffix=".jsonl", handler=lambda p: triggered.append(p))

    router.dispatch(tmp_path / "y.txt")
    assert triggered == []
```

- [ ] **Step 3：跑测试确认失败**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_mirror.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memoryd.mirror'`.

- [ ] **Step 4：实现 `memoryd/src/memoryd/mirror.py`**

```python
"""Filesystem mirror framework: watchdog handlers + _unscoped bucket helper.

`MirrorRouter` dispatches new files to per-suffix handlers (one for Codex
rollout summaries, one for OpenClaw session jsonl). Each handler is
responsible for parsing the file and producing a SessionMemory; this module
provides the common `save_to_scope_or_unscoped` so handlers don't need to
re-implement the fallback for files whose scope can't be resolved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .schema import SessionMemory
from .storage import save_session


UNSCOPED_HASH = "_unscoped"


def save_to_scope_or_unscoped(
    memory_root: Path,
    session: SessionMemory,
    *,
    resolved_scope_hash: str | None,
) -> Path:
    """Save a session under its resolved scope, or the _unscoped bucket.

    If `resolved_scope_hash` is None (handler couldn't reverse-lookup a
    scope), rewrite the frontmatter scope_hash to UNSCOPED_HASH so the
    file ends up under `<root>/scopes/_unscoped/sessions/` instead of a
    misleading hash.
    """
    target_hash = resolved_scope_hash or UNSCOPED_HASH
    if session.frontmatter.scope_hash != target_hash:
        session = session.model_copy(
            update={
                "frontmatter": session.frontmatter.model_copy(
                    update={"scope_hash": target_hash}
                )
            }
        )
    return save_session(memory_root, session)


FileHandler = Callable[[Path], None]


@dataclass
class MirrorRouter:
    """Dispatch new files to per-suffix handlers."""

    _handlers: dict[str, FileHandler] = field(default_factory=dict)

    def register(self, *, suffix: str, handler: FileHandler) -> None:
        self._handlers[suffix.lower()] = handler

    def dispatch(self, path: Path) -> None:
        handler = self._handlers.get(path.suffix.lower())
        if handler is None:
            return
        handler(path)
```

- [ ] **Step 5：跑测试确认通过**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_mirror.py -v`
Expected: 4 passed.

- [ ] **Step 6：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/pyproject.toml memoryd/uv.lock memoryd/src/memoryd/mirror.py memoryd/tests/test_mirror.py
git commit -m "$(cat <<'EOF'
新增 mirror 框架：watchdog handler 公共基类 + _unscoped 兜底桶

为 Plan 2.5 的 Codex rollout / OpenClaw session FS-watch 通路提供共用
基础。MirrorRouter 按 suffix 派发，save_to_scope_or_unscoped 把反推不
到 scope 的条目落到 _unscoped 桶不丢数据。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2：Codex rollout_summary 转码模块

**Files:**
- Create: `memoryd/src/memoryd/mirror_codex.py`
- Create: `memoryd/tests/test_mirror_codex.py`

把 Codex rollout_summary `.md` 文件解析成 SessionMemory(source=codex-rollout)。利用真实格式的 `cwd:` 字段直接拿 scope（不需要内容反推）。

- [ ] **Step 1：写失败测试 `memoryd/tests/test_mirror_codex.py`**

```python
"""Codex rollout_summary mirror tests."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryd.mirror_codex import (
    CodexRolloutHandler,
    parse_rollout_header,
    transcode_rollout,
)
from memoryd.storage import list_sessions


SAMPLE_ROLLOUT = """thread_id: 019e208d-06c4-7ab2-905c-1243dd8a2cd3
updated_at: 2026-05-13T12:46:41+00:00
rollout_path: /Users/abble/.codex/sessions/2026/05/13/rollout-2026-05-13T16-56-13-019e208d.jsonl
cwd: /Users/abble/Moonlight-Radiance-game
git_branch: codex/0-0-1-task0-preproduction

# Built an ElevenLabs audio pipeline for Moonlight Radiance.

Rollout context: discussed audio review workflow.

## Task 1: read handoff
Outcome: success
"""


def test_parse_rollout_header_extracts_kv_pairs(tmp_path: Path):
    f = tmp_path / "sample.md"
    f.write_text(SAMPLE_ROLLOUT)
    header, body = parse_rollout_header(f)
    assert header["thread_id"] == "019e208d-06c4-7ab2-905c-1243dd8a2cd3"
    assert header["cwd"] == "/Users/abble/Moonlight-Radiance-game"
    assert header["git_branch"] == "codex/0-0-1-task0-preproduction"
    assert body.startswith("# Built an ElevenLabs")


def test_parse_rollout_header_tolerates_missing_blank_line(tmp_path: Path):
    """No blank line between header and body still parses."""
    f = tmp_path / "no_blank.md"
    f.write_text("cwd: /tmp/x\n# title\nbody\n")
    header, body = parse_rollout_header(f)
    assert header == {"cwd": "/tmp/x"}
    assert body == "# title\nbody\n"


def test_parse_rollout_returns_empty_header_when_first_line_not_kv(tmp_path: Path):
    f = tmp_path / "no_header.md"
    f.write_text("# title only\n\nbody\n")
    header, body = parse_rollout_header(f)
    assert header == {}
    assert body.startswith("# title")


def test_transcode_uses_cwd_from_header(tmp_path: Path):
    project = tmp_path / "myproj"
    project.mkdir()
    (project / ".git").mkdir()  # makes resolve_scope_root pick this

    rollout = tmp_path / "rollout.md"
    rollout.write_text(SAMPLE_ROLLOUT.replace("/Users/abble/Moonlight-Radiance-game", str(project)))

    session, resolved_hash = transcode_rollout(rollout)
    assert session.frontmatter.source == "codex-rollout"
    assert session.frontmatter.scope_hash == resolved_hash
    assert resolved_hash is not None
    assert "Built an ElevenLabs" in session.body
    assert session.frontmatter.slug.startswith("2026-05-13")  # from updated_at


def test_transcode_returns_none_scope_when_cwd_missing(tmp_path: Path):
    """Rollout without cwd field → resolved_hash is None (UNSCOPED bucket)."""
    rollout = tmp_path / "no_cwd.md"
    rollout.write_text("thread_id: abc\nupdated_at: 2026-05-13T00:00:00+00:00\n\n# t\nbody\n")
    session, resolved_hash = transcode_rollout(rollout)
    assert resolved_hash is None
    assert session.frontmatter.source == "codex-rollout"


def test_handler_writes_session_to_data_root(tmp_path: Path):
    """End-to-end: handler reads file, saves under data root."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()

    rollout = tmp_path / "rollout.md"
    rollout.write_text(SAMPLE_ROLLOUT.replace("/Users/abble/Moonlight-Radiance-game", str(project)))

    data_root = tmp_path / "data"
    data_root.mkdir()

    handler = CodexRolloutHandler(memory_root=data_root)
    handler(rollout)

    from memoryd.scope import scope_hash, resolve_scope_root
    sh = scope_hash(resolve_scope_root(project))
    files = list_sessions(data_root, scope_hash=sh)
    assert len(files) == 1
    assert "codex-rollout" in files[0].read_text()


def test_handler_skips_non_md_files(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    handler = CodexRolloutHandler(memory_root=data_root)
    fake = tmp_path / "not-md.txt"
    fake.write_text("nope")
    handler(fake)  # should be a no-op, not raise
    assert list((data_root / "scopes").glob("**/*")) == []
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_mirror_codex.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memoryd.mirror_codex'`.

- [ ] **Step 3：实现 `memoryd/src/memoryd/mirror_codex.py`**

```python
"""Mirror Codex.app rollout_summary markdown into memoryd SessionMemory.

Codex.app writes a session summary per session to:
    ~/.codex/memories/rollout_summaries/<ISO-ts>-<short-id>-<topic-slug>.md

Header is plain text key:value (NOT YAML frontmatter — no `---`):
    thread_id: <uuid>
    updated_at: <ISO-8601>
    rollout_path: /Users/.../rollout-<ts>-<uuid>.jsonl
    cwd: /Users/.../<project>
    git_branch: <branch>

    # <one-line title>
    <body>

We read `cwd` directly for scope resolution; no content reverse-lookup needed.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .mirror import save_to_scope_or_unscoped
from .schema import Frontmatter, SessionMemory
from .scope import resolve_scope_root, scope_hash


_HEADER_LINE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*?)\s*$")


def parse_rollout_header(path: Path) -> tuple[dict[str, str], str]:
    """Parse Codex rollout_summary into (header dict, body string).

    Stops on first blank line OR first line that doesn't match KEY: VALUE.
    Returns ({}, full_text) when no header is detected.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    header: dict[str, str] = {}
    body_start = 0
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped == "":
            body_start = idx + 1
            break
        m = _HEADER_LINE.match(raw.rstrip("\n"))
        if not m:
            body_start = idx
            break
        header[m.group(1)] = m.group(2)
    else:
        # file is all header, no body
        body_start = len(lines)

    body = "".join(lines[body_start:])
    return header, body


def _slug_from_filename(path: Path, updated_at: datetime | None) -> str:
    """Derive a memoryd slug from a Codex rollout filename + updated_at date.

    Filename pattern: 2026-05-13T08-56-13-xoIz-<topic-slug>.md
    Memoryd slug pattern: <YYYY-MM-DD>-<stem>
    """
    date_str = (updated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    stem = path.stem
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", stem)[:80]
    return f"{date_str}-{safe}"


def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _resolve_scope_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    p = Path(cwd)
    if not p.exists():
        # cwd may point to a project on a machine no longer accessible;
        # still try to resolve as a path-only scope hash (no .git walk)
        return scope_hash(p)
    return scope_hash(resolve_scope_root(p))


def transcode_rollout(path: Path) -> tuple[SessionMemory, str | None]:
    """Read a rollout_summary .md and produce SessionMemory + resolved scope hash.

    Returns (session, None) if scope can't be resolved → caller routes to
    _unscoped bucket via save_to_scope_or_unscoped.
    """
    header, body = parse_rollout_header(path)
    updated_at = _parse_iso(header.get("updated_at"))
    resolved_hash = _resolve_scope_from_cwd(header.get("cwd"))

    slug = _slug_from_filename(path, updated_at)
    # Title: first H1 in body, fallback to filename stem
    title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    # Body keeps the original header as a fenced block so future readers
    # can see thread_id / rollout_path / git_branch.
    header_block = "\n".join(f"{k}: {v}" for k, v in header.items())
    full_body = (
        f"```codex-rollout-header\n{header_block}\n```\n\n{body}"
        if header
        else body
    )

    session = SessionMemory(
        frontmatter=Frontmatter(
            title=title[:200],
            slug=slug,
            type="session",
            scope_hash=resolved_hash or "_unscoped",
            triggers=[],
            source="codex-rollout",
            created_at=updated_at,
        ),
        body=full_body,
    )
    return session, resolved_hash


class CodexRolloutHandler:
    """Callable that mirrors a single rollout_summary .md to memoryd data root."""

    def __init__(self, memory_root: Path) -> None:
        self.memory_root = memory_root

    def __call__(self, path: Path) -> None:
        if path.suffix.lower() != ".md":
            return
        try:
            session, resolved_hash = transcode_rollout(path)
        except Exception:
            # Never crash the daemon on a single bad file.
            return
        save_to_scope_or_unscoped(
            self.memory_root,
            session,
            resolved_scope_hash=resolved_hash,
        )
```

- [ ] **Step 4：跑测试确认通过**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_mirror_codex.py -v`
Expected: 7 passed.

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/mirror_codex.py memoryd/tests/test_mirror_codex.py
git commit -m "$(cat <<'EOF'
新增 Codex rollout_summary 转码模块

读 ~/.codex/memories/rollout_summaries/ 目录下 Codex.app 自动写入的会话
摘要文件，按 cwd: 头字段直接 resolve scope，转成 source=codex-rollout
的 SessionMemory。无 cwd 时落到 _unscoped 桶。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3：OpenClaw session jsonl 转码模块

**Files:**
- Create: `memoryd/src/memoryd/mirror_openclaw.py`
- Create: `memoryd/tests/test_mirror_openclaw.py`

把 OpenClaw `~/.openclaw/agents/<agent-id>/sessions/<session-id>.jsonl` 解析成 SessionMemory(source=openclaw-fs)。OpenClaw session log 没显式 cwd 字段，需做内容扫描反推。

- [ ] **Step 1：写失败测试 `memoryd/tests/test_mirror_openclaw.py`**

```python
"""OpenClaw session jsonl mirror tests."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.mirror_openclaw import (
    OpenClawSessionHandler,
    reverse_lookup_scope_from_content,
    transcode_session_jsonl,
)
from memoryd.storage import list_sessions


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def test_reverse_lookup_finds_deepest_known_root(tmp_path: Path):
    root_shallow = tmp_path / "root"
    root_deep = root_shallow / "nested"
    root_shallow.mkdir()
    root_deep.mkdir()

    content = f"I was editing {root_deep}/src/x.py for a while."
    resolved = reverse_lookup_scope_from_content(
        content,
        known_roots=[root_shallow, root_deep],
    )
    assert resolved == root_deep.resolve()


def test_reverse_lookup_returns_none_when_zero_matches(tmp_path: Path):
    other = tmp_path / "other"
    other.mkdir()
    resolved = reverse_lookup_scope_from_content(
        "no path mentioned at all",
        known_roots=[other],
    )
    assert resolved is None


def test_reverse_lookup_returns_none_when_multiple_unrelated_roots_match(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    content = f"work in {a}/file and also {b}/other-file"
    resolved = reverse_lookup_scope_from_content(content, known_roots=[a, b])
    assert resolved is None  # ambiguous → unscoped


def test_transcode_session_jsonl_extracts_text(tmp_path: Path):
    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(jsonl, [
        {"role": "user", "content": "in /Users/abble/myproj let's fix X"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ])

    project = tmp_path / "Users-abble-myproj"
    project.mkdir()
    (project / ".git").mkdir()

    session, resolved = transcode_session_jsonl(
        jsonl,
        known_roots=[project.parent],  # parent of all candidates
    )
    assert session.frontmatter.source == "openclaw-fs"
    assert "let's fix X" in session.body
    assert "ok" in session.body


def test_transcode_tolerates_malformed_lines(tmp_path: Path):
    jsonl = tmp_path / "bad.jsonl"
    jsonl.write_text('{"role":"user","content":"good"}\nnot-json\n{"role":"x"}\n')
    session, _ = transcode_session_jsonl(jsonl, known_roots=[])
    assert "good" in session.body


def test_handler_saves_to_data_root(tmp_path: Path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / ".git").mkdir()

    jsonl = tmp_path / "sess.jsonl"
    _write_jsonl(jsonl, [
        {"role": "user", "content": f"cwd: {proj} doing work"},
        {"role": "assistant", "content": "ok"},
    ])

    data_root = tmp_path / "data"
    data_root.mkdir()

    handler = OpenClawSessionHandler(memory_root=data_root, known_roots=[proj])
    handler(jsonl)

    from memoryd.scope import scope_hash, resolve_scope_root
    sh = scope_hash(resolve_scope_root(proj))
    files = list_sessions(data_root, scope_hash=sh)
    assert len(files) == 1


def test_handler_routes_to_unscoped_on_ambiguous_content(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    jsonl = tmp_path / "amb.jsonl"
    _write_jsonl(jsonl, [{"role": "user", "content": f"{a} and also {b}"}])
    data_root = tmp_path / "data"
    data_root.mkdir()

    handler = OpenClawSessionHandler(memory_root=data_root, known_roots=[a, b])
    handler(jsonl)

    from memoryd.mirror import UNSCOPED_HASH
    files = list_sessions(data_root, scope_hash=UNSCOPED_HASH)
    assert len(files) == 1
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_mirror_openclaw.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memoryd.mirror_openclaw'`.

- [ ] **Step 3：实现 `memoryd/src/memoryd/mirror_openclaw.py`**

```python
"""Mirror OpenClaw session jsonl files into memoryd SessionMemory.

OpenClaw writes per-session jsonl logs to:
    ~/.openclaw/agents/<agent-id>/sessions/<session-id>.jsonl

Each line is a JSON record; shapes vary but typically contain a `role` /
`author` and `content` (string or array of {type:"text", text:...}).
No explicit cwd field, so we reverse-lookup scope from path mentions in
the concatenated message content.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .mirror import save_to_scope_or_unscoped
from .schema import Frontmatter, SessionMemory
from .scope import resolve_scope_root, scope_hash


_PATH_PATTERN = re.compile(r"(/Users/[^\s\"`'()<>]+)")


def reverse_lookup_scope_from_content(
    content: str,
    *,
    known_roots: list[Path],
) -> Path | None:
    """Find the deepest known root that any path in `content` lies under.

    Returns None if:
    - no path is mentioned
    - multiple unrelated roots (not nested) match
    """
    if not known_roots:
        return None
    resolved_roots = [r.resolve() for r in known_roots]
    candidates = _PATH_PATTERN.findall(content)
    if not candidates:
        return None

    matched: set[Path] = set()
    for cand in candidates:
        cand_path = Path(cand).resolve()
        # find any root that is an ancestor of (or equal to) cand_path
        for root in resolved_roots:
            try:
                cand_path.relative_to(root)
                matched.add(root)
            except ValueError:
                continue

    if not matched:
        return None

    # If matched roots are nested, pick the deepest.
    # If matched roots are siblings / unrelated, ambiguity → None.
    matched_list = sorted(matched, key=lambda p: len(str(p)), reverse=True)
    deepest = matched_list[0]
    for other in matched_list[1:]:
        try:
            deepest.relative_to(other)  # other is ancestor of deepest → OK
        except ValueError:
            try:
                other.relative_to(deepest)  # deepest ancestor of other → impossible here since sorted by length
                # If this somehow passes, treat as ambiguous
                return None
            except ValueError:
                # truly unrelated
                return None
    return deepest


def _extract_text(content_field) -> str | None:
    if isinstance(content_field, str):
        return content_field
    if isinstance(content_field, list):
        parts = []
        for c in content_field:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict):
                txt = c.get("text") or c.get("value")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts) if parts else None
    return None


def transcode_session_jsonl(
    path: Path,
    *,
    known_roots: list[Path],
) -> tuple[SessionMemory, Path | None]:
    """Read OpenClaw session jsonl, return (SessionMemory, resolved_root)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    body_parts: list[str] = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        role = obj.get("role") or obj.get("author") or "?"
        text = _extract_text(obj.get("content"))
        if text:
            body_parts.append(f"**{role}**: {text}")

    body = "\n\n".join(body_parts) if body_parts else "(empty session)"

    # Reverse-lookup scope
    resolved_root = reverse_lookup_scope_from_content(body, known_roots=known_roots)
    resolved_hash: str | None = None
    if resolved_root is not None:
        resolved_hash = scope_hash(resolve_scope_root(resolved_root))

    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) if path.exists() else datetime.now(timezone.utc)
    slug = f"{mtime:%Y-%m-%d}-{path.stem}"

    session = SessionMemory(
        frontmatter=Frontmatter(
            title=f"OpenClaw session {path.stem[:20]}",
            slug=slug,
            type="session",
            scope_hash=resolved_hash or "_unscoped",
            triggers=[],
            source="openclaw-fs",
            created_at=mtime,
        ),
        body=body[:8000],  # cap body; Plan 3 LLM-summarizes
    )
    return session, resolved_root


class OpenClawSessionHandler:
    """Callable that mirrors a single OpenClaw session.jsonl to memoryd."""

    def __init__(self, memory_root: Path, known_roots: list[Path]) -> None:
        self.memory_root = memory_root
        self.known_roots = known_roots

    def __call__(self, path: Path) -> None:
        if path.suffix.lower() != ".jsonl":
            return
        try:
            session, resolved_root = transcode_session_jsonl(
                path, known_roots=self.known_roots
            )
        except Exception:
            return
        resolved_hash: str | None = None
        if resolved_root is not None:
            resolved_hash = scope_hash(resolve_scope_root(resolved_root))
        save_to_scope_or_unscoped(
            self.memory_root,
            session,
            resolved_scope_hash=resolved_hash,
        )
```

- [ ] **Step 4：跑测试确认通过**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_mirror_openclaw.py -v`
Expected: 7 passed.

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/mirror_openclaw.py memoryd/tests/test_mirror_openclaw.py
git commit -m "$(cat <<'EOF'
新增 OpenClaw session jsonl 转码模块

读 ~/.openclaw/agents/*/sessions/*.jsonl，按 known_roots 内容反推 scope
（最深匹配胜出；多匹配 / 零匹配 → _unscoped），转成 source=openclaw-fs
的 SessionMemory。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4：`memoryd mirror` CLI 子命令

**Files:**
- Modify: `memoryd/src/memoryd/cli.py`
- Modify: `memoryd/tests/test_cli.py`

加 `memoryd mirror [--codex] [--openclaw] [--known-roots PATH ...] [--once]` 子命令。`--once` 模式跑一次扫描已有文件后退出（给单测/手测用）；缺省常驻 watchdog observer。

- [ ] **Step 1：在 `memoryd/tests/test_cli.py` 末尾追加（不删现有）**

```python
def test_mirror_once_scans_existing_codex_files(memory_root: Path, tmp_path: Path):
    """`memoryd mirror --codex --once --codex-dir <tmp>` mirrors existing files then exits."""
    import subprocess
    import os
    import re as _re

    codex_dir = tmp_path / "rollout_summaries"
    codex_dir.mkdir()
    sample = (
        "thread_id: t1\nupdated_at: 2026-05-14T10:00:00+00:00\n"
        f"cwd: {tmp_path}\n\n# title\nbody\n"
    )
    (codex_dir / "2026-05-14T10-00-00-id1-topic.md").write_text(sample)

    proc = subprocess.run(
        [
            "uv", "run", "memoryd", "mirror",
            "--once",
            "--codex-dir", str(codex_dir),
        ],
        capture_output=True,
        text=True,
        cwd="/Users/abble/project-management-personal/memoryd",
        env={**os.environ, "MEMORYD_DATA_ROOT": str(memory_root)},
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    files = list((memory_root / "scopes").rglob("*.md"))
    assert len(files) == 1
    assert "codex-rollout" in files[0].read_text()


def test_mirror_help_includes_subcommand():
    import subprocess
    proc = subprocess.run(
        ["uv", "run", "memoryd", "mirror", "--help"],
        capture_output=True,
        text=True,
        cwd="/Users/abble/project-management-personal/memoryd",
        timeout=20,
    )
    assert proc.returncode == 0
    assert "--codex" in proc.stdout
    assert "--openclaw" in proc.stdout
    assert "--once" in proc.stdout
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_cli.py::test_mirror_help_includes_subcommand -v`
Expected: FAIL（输出含 "invalid choice: 'mirror'" 或 "unrecognized arguments"）。

- [ ] **Step 3：修改 `memoryd/src/memoryd/cli.py`**

在文件末尾的 `main()` 函数前面（即 `def main() -> int:` 上方），加导入：

```python
from .mirror import MirrorRouter
from .mirror_codex import CodexRolloutHandler
from .mirror_openclaw import OpenClawSessionHandler
```

把现有的 `main()` 函数（约在文件底部，含 `parser = argparse.ArgumentParser(prog="memoryd")` 那一段）保留不动，**在 `cmd_capture` 函数之后、`main` 函数之前** 加：

```python
def _build_router_for_args(args: argparse.Namespace, memory_root: Path) -> MirrorRouter:
    router = MirrorRouter()
    if args.codex:
        router.register(suffix=".md", handler=CodexRolloutHandler(memory_root))
    if args.openclaw:
        known = [Path(p) for p in (args.known_roots or [])]
        router.register(
            suffix=".jsonl",
            handler=OpenClawSessionHandler(memory_root, known_roots=known),
        )
    return router


def _watch_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    if args.codex:
        codex_dir = Path(args.codex_dir or (Path.home() / ".codex" / "memories" / "rollout_summaries"))
        codex_dir.mkdir(parents=True, exist_ok=True)
        paths.append(codex_dir)
    if args.openclaw:
        openclaw_root = Path(args.openclaw_dir or (Path.home() / ".openclaw" / "agents"))
        openclaw_root.mkdir(parents=True, exist_ok=True)
        paths.append(openclaw_root)
    return paths


def cmd_mirror(args: argparse.Namespace) -> int:
    if not args.codex and not args.openclaw:
        print("error: pass at least one of --codex / --openclaw", file=sys.stderr)
        return 2

    memory_root = _data_root()
    router = _build_router_for_args(args, memory_root)
    paths = _watch_paths(args)

    # First, scan existing files in target dirs once (catch-up pass)
    for watch_root in paths:
        for f in watch_root.rglob("*"):
            if f.is_file():
                router.dispatch(f)

    if args.once:
        return 0

    # Run watchdog observer until SIGINT
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class _Adapter(FileSystemEventHandler):
        def __init__(self, router: MirrorRouter) -> None:
            self.router = router
        def on_created(self, event):
            if event.is_directory:
                return
            self.router.dispatch(Path(event.src_path))
        def on_modified(self, event):
            # Some apps write files in two steps; re-dispatch on modify too.
            if event.is_directory:
                return
            self.router.dispatch(Path(event.src_path))

    observer = Observer()
    adapter = _Adapter(router)
    for p in paths:
        observer.schedule(adapter, str(p), recursive=True)
    observer.start()
    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join(timeout=5)
    return 0
```

修改 `main()` 函数中的 subparser 注册部分。当前 `main()` 是（约文件末尾）：

```python
def main() -> int:
    parser = argparse.ArgumentParser(prog="memoryd")
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_capture = subs.add_parser("capture", help="read SessionEnd payload from stdin and save")
    p_capture.add_argument(
        "--source",
        default="claude-code",
        help="origin tool tag written to frontmatter (claude-code | codex | openclaw | ...)",
    )
    p_capture.set_defaults(func=cmd_capture)

    args = parser.parse_args()
    return args.func(args)
```

在 `p_capture.set_defaults(...)` 之后、`args = parser.parse_args()` 之前插入：

```python
    p_mirror = subs.add_parser(
        "mirror",
        help="watch Codex / OpenClaw session log dirs and mirror new files into memoryd",
    )
    p_mirror.add_argument("--codex", action="store_true", help="mirror Codex rollout_summaries")
    p_mirror.add_argument("--openclaw", action="store_true", help="mirror OpenClaw session jsonl")
    p_mirror.add_argument(
        "--codex-dir",
        default=None,
        help="override Codex rollout dir (default: ~/.codex/memories/rollout_summaries)",
    )
    p_mirror.add_argument(
        "--openclaw-dir",
        default=None,
        help="override OpenClaw agents root (default: ~/.openclaw/agents)",
    )
    p_mirror.add_argument(
        "--known-roots",
        nargs="*",
        default=None,
        help="paths to use for OpenClaw content-based scope reverse-lookup",
    )
    p_mirror.add_argument(
        "--once",
        action="store_true",
        help="scan existing files once and exit (no watchdog)",
    )
    p_mirror.set_defaults(func=cmd_mirror)
```

- [ ] **Step 4：跑测试确认通过**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_cli.py -v -k mirror`
Expected: 2 passed.

跑全量回归：

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest -v`
Expected: 34 + 4 (mirror) + 7 (mirror_codex) + 7 (mirror_openclaw) + 2 (cli mirror) = 54 passed。

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/cli.py memoryd/tests/test_cli.py
git commit -m "$(cat <<'EOF'
加 memoryd mirror 子命令：watchdog 双通道 daemon

--codex 监听 ~/.codex/memories/rollout_summaries/，--openclaw 监听
~/.openclaw/agents/*/sessions/。--once 跑一次启动扫描后退出，缺省常驻
watchdog observer。launchd plist 启动用这个进程。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5：Codex notify 探针 + wrapper 脚本

**Files:**
- Create: `scripts/codex-notify-probe.sh`
- Create: `scripts/codex-notify-wrapper.sh`

Probe 脚本：log argv / stdin / env 到 `~/.local/share/memoryd/probe/notify-probe.log`，**不调用** SkyComputerUseClient（保 probe 期间 Codex Computer Use 暂时停摆——Phase 1 临时操作，跑完一轮即换回）。Wrapper 脚本：先 exec 原 SkyComputerUseClient 透传所有 argv / stdin / env，**再**用 `printf '%s' | memoryd capture --source codex-notify` 后台 fork。

- [ ] **Step 1：写 probe `scripts/codex-notify-probe.sh`**

```bash
#!/usr/bin/env bash
# Plan 2.5 Phase 1 用户探针：log Codex notify 实际收到什么。
# 临时替换 ~/.codex/config.toml 的 notify field；跑一轮 Codex turn 后
# 把日志粘回给 subagent 用来 design wrapper，然后立刻换回原 notify。
#
# WARNING: 探针运行期间 Codex Computer Use（SkyComputerUseClient）不
# 工作，因为这个脚本不透传调用。Phase 1 完成一次探测即换回。

set -euo pipefail

LOG_DIR="${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/probe"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/notify-probe.log"

{
    printf '=== probe fire at %s ===\n' "$(date -Iseconds)"
    printf 'argv (%d): ' "$#"
    if [[ $# -gt 0 ]]; then
        printf '%q ' "$@"
    fi
    printf '\n\n'

    printf 'stdin (up to 8KB):\n'
    # Read stdin if available, but don't block forever
    if [[ -p /dev/stdin ]] || [[ ! -t 0 ]]; then
        head -c 8192 <&0 || true
        printf '\n[end stdin]\n'
    else
        printf '[no stdin pipe]\n'
    fi
    printf '\n'

    printf 'env (filtered):\n'
    env | grep -iE '^(CODEX|OPENAI|SESSION|TURN|NOTIFY|HOME|USER|PATH)' | sort
    printf '=== probe end ===\n\n'
} >> "$LOG_FILE" 2>&1

exit 0
```

让脚本可执行：

```bash
chmod +x /Users/abble/project-management-personal/scripts/codex-notify-probe.sh
```

- [ ] **Step 2：写 wrapper `scripts/codex-notify-wrapper.sh`**

```bash
#!/usr/bin/env bash
# Codex notify wrapper:
#   1. exec the original notify target (Codex Computer Use) transparently
#   2. fork memoryd capture in background with whatever payload was provided
#
# `~/.codex/config.toml` notify is rewritten to:
#   notify = [".../codex-notify-wrapper.sh", "turn-ended"]
# All arguments after the path are passed through. The original notify
# target path is stored in CODEX_NOTIFY_ORIGINAL env (set by setup CLI)
# so we know what to invoke.
#
# Failures NEVER block Codex; we always exit 0 unless the original notify
# returns nonzero (we honor its exit code).

set -uo pipefail

LOG_DIR="${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/codex-notify.log"

ORIGINAL="${CODEX_NOTIFY_ORIGINAL:-}"

# Buffer stdin (we may need it for both the original target and memoryd)
PAYLOAD=""
if [[ ! -t 0 ]]; then
    PAYLOAD="$(cat || true)"
fi

# 1. Transparently call the original notify target (Computer Use, etc.)
ORIGINAL_EXIT=0
if [[ -n "$ORIGINAL" ]] && [[ -x "$ORIGINAL" ]]; then
    if [[ -n "$PAYLOAD" ]]; then
        printf '%s' "$PAYLOAD" | "$ORIGINAL" "$@"
    else
        "$ORIGINAL" "$@" </dev/null
    fi
    ORIGINAL_EXIT=$?
fi

# 2. Fork memoryd capture (best-effort, never blocks Codex)
MEMORYD_BIN="${MEMORYD_BIN:-/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd}"
if [[ -x "$MEMORYD_BIN" ]]; then
    (
        # Build a minimal JSON payload from argv + cwd guess; the CLI is
        # tolerant of unknown / missing keys (plan 1 behavior). If argv
        # contains JSON on stdin already, prefer that.
        if [[ -n "$PAYLOAD" ]] && printf '%s' "$PAYLOAD" | python3 -c 'import json,sys; json.loads(sys.stdin.read())' >/dev/null 2>&1; then
            # stdin was JSON — use it verbatim
            printf '%s' "$PAYLOAD" | "$MEMORYD_BIN" capture --source codex-notify >> "$LOG_FILE" 2>&1
        else
            # synthesize from argv
            SESSION_ID="codex-notify-$(date +%s)-$$"
            python3 -c "
import json, os, sys
print(json.dumps({
    'session_id': '$SESSION_ID',
    'transcript_path': '',
    'cwd': os.environ.get('PWD', '/'),
    'argv': sys.argv[1:],
}))
" -- "$@" | "$MEMORYD_BIN" capture --source codex-notify >> "$LOG_FILE" 2>&1
        fi
        printf '%s  capture done\n' "$(date -Iseconds)" >> "$LOG_FILE"
    ) &
    disown $! 2>/dev/null || true
fi

exit "$ORIGINAL_EXIT"
```

让脚本可执行：

```bash
chmod +x /Users/abble/project-management-personal/scripts/codex-notify-wrapper.sh
```

- [ ] **Step 3：手工烟雾测试 probe**

```bash
cd /Users/abble/project-management-personal
TMPDIR=$(mktemp -d)
export MEMORYD_DATA_ROOT="$TMPDIR/data"
printf '{"session":"test"}' | scripts/codex-notify-probe.sh turn-ended arg1 arg2
cat "$TMPDIR/data/probe/notify-probe.log"
```

Expected: 输出含 `argv (3): turn-ended arg1 arg2`、`stdin (up to 8KB): {"session":"test"}`、`env (filtered): ...`、`=== probe end ===`。

- [ ] **Step 4：手工烟雾测试 wrapper（mock original）**

```bash
cd /Users/abble/project-management-personal
TMPDIR=$(mktemp -d)
export MEMORYD_DATA_ROOT="$TMPDIR/data"

# Make a fake "original notify" that just echoes its argv to a log
FAKE_ORIG="$TMPDIR/fake-original.sh"
cat > "$FAKE_ORIG" <<'FAKE'
#!/usr/bin/env bash
printf 'fake-original called with: %s\n' "$*" > "$1.log" 2>/dev/null || true
exit 0
FAKE
chmod +x "$FAKE_ORIG"

export CODEX_NOTIFY_ORIGINAL="$FAKE_ORIG"
export MEMORYD_BIN="/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd"

# Run wrapper with stdin payload that is valid JSON
printf '{"session_id":"smoke-1","transcript_path":"","cwd":"%s"}' "$TMPDIR" \
    | scripts/codex-notify-wrapper.sh turn-ended

sleep 1
echo "--- notify log ---"
cat "$TMPDIR/data/logs/codex-notify.log"
echo "--- captured memory ---"
find "$TMPDIR/data/scopes" -name "*.md"
```

Expected:
- `notify log` 含一行 `capture done`
- `find` 输出 1 个 `.md` 文件
- 文件 frontmatter 含 `source: codex-notify`

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add scripts/codex-notify-probe.sh scripts/codex-notify-wrapper.sh
git commit -m "$(cat <<'EOF'
新增 Codex notify probe + wrapper 脚本

probe 临时替换 notify field 用来探 argv/stdin/env；wrapper 透传原
SkyComputerUseClient（CODEX_NOTIFY_ORIGINAL）+ 后台 fork memoryd
capture --source codex-notify。stdin 是合法 JSON 时直接管道，否则
合成 minimal payload。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6：OpenClaw 插件 SDK 重写

**Files:**
- Create: `scripts/openclaw-memoryd-plugin/src/payload.js`
- Create: `scripts/openclaw-memoryd-plugin/src/register.js`
- Create: `scripts/openclaw-memoryd-plugin/src/index.js`
- Create: `scripts/openclaw-memoryd-plugin/openclaw.plugin.json`
- Modify: `scripts/openclaw-memoryd-plugin/package.json`
- Create: `scripts/openclaw-memoryd-plugin/tests/payload.test.mjs`
- Create: `scripts/openclaw-memoryd-plugin/tests/register.test.mjs`
- Delete: `scripts/openclaw-memoryd-plugin/src/index.mjs`
- Delete: `scripts/openclaw-memoryd-plugin/tests/index.test.mjs`

把现有 index.mjs 的纯函数（normalizeMessage / materializeTranscript / buildPayload / spawnCapture / logFor / tsLine）抽到 `payload.js`（保留所有 7 个单测）。在 `register.js` 里实现 makeHandler / makeSubscription（**不** import `@openclaw/plugin-sdk`，方便单测）。`index.js` 是 ~10 行 SDK glue（`definePluginEntry({ register(api) { api.registerAgentEventSubscription(makeSubscription()) } })`）。Manifest `openclaw.plugin.json` 描述插件契约。

- [ ] **Step 1：写 `scripts/openclaw-memoryd-plugin/src/payload.js`**

把现有 `src/index.mjs` 里的所有 helper 函数（`logFor`, `tsLine`, `normalizeMessage`, `materializeTranscript`, `buildPayload`, `spawnCapture` 以及 `DEFAULT_MEMORYD_BIN`）原封不动 copy 到 `src/payload.js`，**只**改 import 路径——确保 export 这些符号。完整内容：

```javascript
/**
 * Pure helpers shared between SDK entry (index.js) and tests (payload.test.mjs).
 * No OpenClaw SDK imports; safe to import directly in node --test.
 */

import { spawn } from "node:child_process";
import { mkdirSync, appendFileSync, existsSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

export const DEFAULT_MEMORYD_BIN =
  "/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd";

export function logFor() {
  const dataRoot =
    process.env.MEMORYD_DATA_ROOT || join(homedir(), ".local", "share", "memoryd");
  const logDir = join(dataRoot, "logs");
  if (!existsSync(logDir)) mkdirSync(logDir, { recursive: true });
  return join(logDir, "openclaw-events.log");
}

export function tsLine(extra) {
  return `${new Date().toISOString()}  ${extra}\n`;
}

export function normalizeMessage(m) {
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

  return { type, message: { content: [{ type: "text", text }] } };
}

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
```

- [ ] **Step 2：写 `scripts/openclaw-memoryd-plugin/src/register.js`**

```javascript
/**
 * Plugin registration logic — no OpenClaw SDK imports, so tests can
 * exercise this module directly with a mock api.
 *
 * Strategy: subscribe to OpenClaw's `lifecycle` event stream. For each
 * received event, attempt to build a memoryd capture payload. If the
 * event carries `cwd` or `messages` we spawn capture; otherwise we just
 * append a one-line marker to the events log (so Phase 1 user can
 * inspect what OpenClaw actually emits and we can narrow the filter in
 * a follow-up plan).
 */

import { appendFileSync } from "node:fs";
import { buildPayload, logFor, spawnCapture, tsLine } from "./payload.js";

export function makeHandler({ spawn = spawnCapture, log = appendFileSync } = {}) {
  return async (event, _ctx) => {
    try {
      const logPath = logFor();
      // Always log the event type (cheap diagnostic for Phase 1 tuning)
      const evtSummary = JSON.stringify({
        ts: new Date().toISOString(),
        stream: event?.stream,
        type: event?.type || event?.data?.type,
        hasMessages: Array.isArray(event?.messages),
        hasCwd: typeof event?.cwd === "string",
      });
      log(logPath, evtSummary + "\n");

      // Capture only when there's something to capture
      const looksCapturable =
        Array.isArray(event?.messages) || typeof event?.cwd === "string";
      if (!looksCapturable) return;

      const payload = buildPayload(event);
      spawn(payload);
    } catch (err) {
      try {
        appendFileSync(logFor(), tsLine(`handler error: ${err?.message || err}`));
      } catch (_) {
        /* swallow */
      }
    }
  };
}

export function makeSubscription({ id = "memoryd-capture", spawn, log } = {}) {
  return {
    id,
    description: "Mirror OpenClaw turn-end events into memoryd",
    streams: ["lifecycle"],
    handle: makeHandler({ spawn, log }),
  };
}
```

- [ ] **Step 3：写 `scripts/openclaw-memoryd-plugin/src/index.js`（SDK entry）**

```javascript
/**
 * OpenClaw plugin entry. Loaded by `openclaw plugins install --force .`
 *
 * Indirection via register.js lets us unit-test the subscription shape
 * without needing the SDK installed in this repo's node_modules.
 */

import { definePluginEntry } from "@openclaw/plugin-sdk";
import { makeSubscription } from "./register.js";

export default definePluginEntry({
  id: "memoryd-openclaw",
  name: "memoryd OpenClaw bridge",
  description: "Mirror OpenClaw turn-end events into the local memoryd data root.",
  kind: "memory",
  register(api) {
    api.registerAgentEventSubscription(makeSubscription());
  },
});
```

- [ ] **Step 4：写 manifest `scripts/openclaw-memoryd-plugin/openclaw.plugin.json`**

```json
{
  "id": "memoryd-openclaw",
  "name": "memoryd OpenClaw bridge",
  "kind": "memory",
  "activation": {
    "onStartup": true
  },
  "configSchema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {}
  },
  "permissions": {
    "allowConversationAccess": true
  }
}
```

- [ ] **Step 5：更新 `scripts/openclaw-memoryd-plugin/package.json`**

完整内容：

```json
{
  "name": "@memoryd/openclaw-plugin",
  "version": "0.2.0",
  "private": true,
  "type": "module",
  "description": "OpenClaw plugin: subscribe to lifecycle events, mirror turn-end into memoryd (source=openclaw).",
  "main": "src/index.js",
  "exports": {
    ".": "./src/index.js",
    "./register": "./src/register.js",
    "./payload": "./src/payload.js"
  },
  "openclaw": {
    "extensions": ["./src/index.js"]
  },
  "scripts": {
    "test": "node --test 'tests/*.test.mjs'"
  },
  "engines": {
    "node": ">=18"
  }
}
```

- [ ] **Step 6：写 `scripts/openclaw-memoryd-plugin/tests/payload.test.mjs`**

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildPayload, materializeTranscript } from "../src/payload.js";

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
  assert.equal(JSON.parse(lines[0]).message.content[0].text, "你好");
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

- [ ] **Step 7：写 `scripts/openclaw-memoryd-plugin/tests/register.test.mjs`**

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeHandler, makeSubscription } from "../src/register.js";

test("subscription has expected shape", () => {
  const sub = makeSubscription();
  assert.equal(sub.id, "memoryd-capture");
  assert.deepEqual(sub.streams, ["lifecycle"]);
  assert.equal(typeof sub.handle, "function");
});

test("handler invokes spawn when event has messages", async () => {
  const spawned = [];
  const handler = makeHandler({
    spawn: (payload) => spawned.push(payload),
    log: () => {},  // suppress diagnostic log writes during test
  });
  await handler({
    stream: "lifecycle",
    sessionId: "s1",
    cwd: "/tmp/a",
    messages: [{ role: "user", content: "hi" }],
  });
  assert.equal(spawned.length, 1);
  assert.equal(spawned[0].session_id, "s1");
});

test("handler skips spawn for irrelevant events", async () => {
  const spawned = [];
  const handler = makeHandler({
    spawn: (payload) => spawned.push(payload),
    log: () => {},
  });
  await handler({ stream: "lifecycle", type: "agent_start" });
  await handler({ stream: "lifecycle" });
  assert.equal(spawned.length, 0);
});

test("handler logs every event regardless of spawn", async () => {
  const logged = [];
  const handler = makeHandler({
    spawn: () => {},
    log: (_path, line) => logged.push(line),
  });
  await handler({ stream: "lifecycle", type: "x" });
  await handler({ stream: "lifecycle", cwd: "/y", messages: [] });
  assert.equal(logged.length, 2);
  assert.ok(logged[0].includes("\"stream\":\"lifecycle\""));
});

test("handler swallows errors thrown by spawn", async () => {
  const handler = makeHandler({
    spawn: () => { throw new Error("boom"); },
    log: () => {},
  });
  // Should not reject:
  await handler({ stream: "lifecycle", cwd: "/x", messages: [{ role: "user", content: "h" }] });
});
```

- [ ] **Step 8：删除老文件**

```bash
cd /Users/abble/project-management-personal
rm scripts/openclaw-memoryd-plugin/src/index.mjs
rm scripts/openclaw-memoryd-plugin/tests/index.test.mjs
```

- [ ] **Step 9：跑测试**

```bash
cd /Users/abble/project-management-personal/scripts/openclaw-memoryd-plugin && npm test
```

Expected: `tests 12` (7 payload + 5 register) `pass 12 fail 0`.

- [ ] **Step 10：Commit**

```bash
cd /Users/abble/project-management-personal
git add scripts/openclaw-memoryd-plugin/
git commit -m "$(cat <<'EOF'
重写 OpenClaw 插件以适配 SDK 2026.5.7（definePluginEntry）

- 抽 payload.js（pure helpers，单测原 7 个保留并通过）
- 新 register.js：makeHandler / makeSubscription，不 import SDK，可单测
- 新 index.js：~10 行 SDK glue，definePluginEntry + registerAgentEventSubscription
- openclaw.plugin.json manifest
- package.json 加 openclaw.extensions 字段
- 删老 index.mjs / index.test.mjs

订阅 lifecycle stream；handler 中所有事件都日志（Phase 1 用户可看
~/.local/share/memoryd/logs/openclaw-events.log 决定下一步 narrow
filter），有 messages 或 cwd 字段时触发 memoryd capture。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7：`memoryd setup` 子命令（用户配置管理）+ launchd plist

**Files:**
- Create: `memoryd/src/memoryd/setup.py`
- Modify: `memoryd/src/memoryd/cli.py`
- Create: `memoryd/tests/test_setup.py`
- Create: `scripts/launchd/com.memoryd.mirror.plist`

memoryd 加 `setup` 子命令组，**全部用 Python tomllib/json read-mutate-write + backup 到 `~/.claude/backups/`**：

- `memoryd setup swap-codex-notify --to probe|wrapper|original`
- `memoryd setup remove-codex-stop-hook`
- `memoryd setup install-launchd-mirror`
- `memoryd setup uninstall-launchd-mirror`

所有子命令支持 `--config-dir PATH` 覆盖默认 `~/.codex/` / `~/Library/LaunchAgents/`（便于单测）。

- [ ] **Step 1：写 launchd plist 模板 `scripts/launchd/com.memoryd.mirror.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.memoryd.mirror</string>
  <key>ProgramArguments</key>
  <array>
    <string>__MEMORYD_BIN__</string>
    <string>mirror</string>
    <string>--codex</string>
    <string>--openclaw</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>MEMORYD_DATA_ROOT</key>
    <string>__MEMORYD_DATA_ROOT__</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
    <key>Crashed</key>
    <true/>
  </dict>
  <key>StandardOutPath</key>
  <string>__MEMORYD_DATA_ROOT__/logs/mirror.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>__MEMORYD_DATA_ROOT__/logs/mirror.stderr.log</string>
</dict>
</plist>
```

`__MEMORYD_BIN__` / `__MEMORYD_DATA_ROOT__` 是占位符；`memoryd setup install-launchd-mirror` 会渲染到 `~/Library/LaunchAgents/com.memoryd.mirror.plist`。

- [ ] **Step 2：写失败测试 `memoryd/tests/test_setup.py`**

```python
"""memoryd setup subcommand tests."""
import json
import shutil
import tomllib
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.setup import (
    backup_file,
    install_launchd_mirror,
    remove_codex_stop_hook,
    swap_codex_notify,
)


_SAMPLE_TOML = """\
model = "gpt-5.5"
notify = ["/Applications/Codex Computer Use.app/SkyComputerUseClient", "turn-ended"]

[mcp_servers.feishu]
command = "node"
args = ["/x"]

[features]
memories = true
"""


def test_backup_file_creates_timestamped_copy(tmp_path: Path):
    src = tmp_path / "config.toml"
    src.write_text(_SAMPLE_TOML)
    backup_dir = tmp_path / "backups"
    bp = backup_file(src, backup_dir=backup_dir)
    assert bp.exists()
    assert bp.read_text() == _SAMPLE_TOML
    assert bp.parent == backup_dir


def test_swap_codex_notify_to_probe_preserves_other_keys(tmp_path: Path):
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    cfg = codex_dir / "config.toml"
    cfg.write_text(_SAMPLE_TOML)
    backup_dir = tmp_path / "backups"

    state_file = swap_codex_notify(
        to="probe",
        codex_dir=codex_dir,
        backup_dir=backup_dir,
        probe_path="/path/to/probe.sh",
        wrapper_path="/path/to/wrapper.sh",
    )
    assert state_file.exists()  # state file remembers original notify

    data = tomllib.loads(cfg.read_text())
    assert data["notify"][0].endswith("probe.sh")
    # other keys untouched
    assert data["model"] == "gpt-5.5"
    assert "mcp_servers" in data
    assert data["features"]["memories"] is True


def test_swap_to_wrapper_includes_original_notify_in_state(tmp_path: Path):
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    cfg = codex_dir / "config.toml"
    cfg.write_text(_SAMPLE_TOML)
    backup_dir = tmp_path / "backups"

    swap_codex_notify(
        to="wrapper",
        codex_dir=codex_dir,
        backup_dir=backup_dir,
        probe_path="/p",
        wrapper_path="/w",
    )
    state = json.loads((codex_dir / ".memoryd-notify-state.json").read_text())
    assert state["original"][0].startswith("/Applications/")


def test_swap_back_to_original(tmp_path: Path):
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    cfg = codex_dir / "config.toml"
    cfg.write_text(_SAMPLE_TOML)
    backup_dir = tmp_path / "backups"

    swap_codex_notify(to="wrapper", codex_dir=codex_dir, backup_dir=backup_dir, probe_path="/p", wrapper_path="/w")
    swap_codex_notify(to="original", codex_dir=codex_dir, backup_dir=backup_dir, probe_path="/p", wrapper_path="/w")

    data = tomllib.loads(cfg.read_text())
    assert data["notify"][0].startswith("/Applications/")


def test_remove_codex_stop_hook_drops_only_stop_entry(tmp_path: Path):
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    hooks = codex_dir / "hooks.json"
    hooks.write_text(json.dumps({
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "/x/codex-stop-hook.sh"}]}],
            "OtherEvent": [{"hooks": [{"type": "command", "command": "/y/keep.sh"}]}],
        }
    }))
    backup_dir = tmp_path / "backups"

    remove_codex_stop_hook(codex_dir=codex_dir, backup_dir=backup_dir)

    data = json.loads(hooks.read_text())
    assert "Stop" not in data["hooks"]
    assert "OtherEvent" in data["hooks"]


def test_install_launchd_mirror_renders_template(tmp_path: Path):
    template_src = tmp_path / "template.plist"
    template_src.write_text("<plist>__MEMORYD_BIN__ __MEMORYD_DATA_ROOT__</plist>")
    launch_dir = tmp_path / "LaunchAgents"
    launch_dir.mkdir()

    install_launchd_mirror(
        template_path=template_src,
        launch_dir=launch_dir,
        memoryd_bin="/path/to/bin",
        data_root="/path/to/data",
    )
    out = launch_dir / "com.memoryd.mirror.plist"
    assert out.exists()
    txt = out.read_text()
    assert "/path/to/bin" in txt
    assert "/path/to/data" in txt
    assert "__MEMORYD_BIN__" not in txt
```

- [ ] **Step 3：跑测试确认失败**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memoryd.setup'`.

- [ ] **Step 4：实现 `memoryd/src/memoryd/setup.py`**

```python
"""User config management for Plan 2.5 wire-up.

All edits to ~/.codex/* and ~/Library/LaunchAgents/* go through this
module: backup → read → mutate → atomic write. Never sed/awk/jq.
"""
from __future__ import annotations

import json
import shutil
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Literal


def backup_file(path: Path, *, backup_dir: Path) -> Path:
    """Copy `path` to `<backup_dir>/<name>.bak.<YYYYMMDD-HHMMSS>`.

    Creates `backup_dir` if it doesn't exist. Returns the backup path.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bp = backup_dir / f"{path.name}.bak.{ts}"
    shutil.copy2(path, bp)
    return bp


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Codex notify swap
# ---------------------------------------------------------------------------

NotifyTarget = Literal["probe", "wrapper", "original"]


def _toml_set_notify(toml_text: str, new_notify: list[str]) -> str:
    """Replace the top-level `notify = [...]` line in a TOML config.

    Uses string-level rewrite because tomllib has no writer and we want
    to preserve formatting of unrelated keys.
    """
    import re
    pattern = re.compile(r"^notify\s*=\s*\[.*?\]\s*$", re.MULTILINE | re.DOTALL)
    rendered = "notify = " + json.dumps(new_notify)
    if pattern.search(toml_text):
        return pattern.sub(rendered, toml_text, count=1)
    # No existing notify — append before first [section]
    section_match = re.search(r"^\[", toml_text, re.MULTILINE)
    if section_match:
        idx = section_match.start()
        return toml_text[:idx] + rendered + "\n\n" + toml_text[idx:]
    return toml_text + ("\n" if not toml_text.endswith("\n") else "") + rendered + "\n"


def swap_codex_notify(
    *,
    to: NotifyTarget,
    codex_dir: Path,
    backup_dir: Path,
    probe_path: str,
    wrapper_path: str,
) -> Path:
    """Rewrite `~/.codex/config.toml`'s notify field; preserve everything else.

    Stores the original notify value in `<codex_dir>/.memoryd-notify-state.json`
    on first swap so we can restore via `to="original"`. Returns the path
    of the state file.
    """
    cfg = codex_dir / "config.toml"
    if not cfg.exists():
        raise FileNotFoundError(cfg)

    state_file = codex_dir / ".memoryd-notify-state.json"
    backup_file(cfg, backup_dir=backup_dir)

    toml_text = cfg.read_text(encoding="utf-8")
    parsed = tomllib.loads(toml_text)
    current = parsed.get("notify", [])

    # Snapshot original on first swap
    if not state_file.exists():
        state = {"original": current}
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    else:
        state = json.loads(state_file.read_text(encoding="utf-8"))

    if to == "probe":
        new_notify = [probe_path]
    elif to == "wrapper":
        # wrapper takes the original notify args verbatim after its own path
        original_args = state["original"][1:] if len(state["original"]) > 1 else []
        new_notify = [wrapper_path, *original_args]
    else:  # "original"
        new_notify = state["original"]

    new_text = _toml_set_notify(toml_text, new_notify)
    _atomic_write(cfg, new_text)
    return state_file


# ---------------------------------------------------------------------------
# Codex hooks.json Stop entry removal
# ---------------------------------------------------------------------------


def remove_codex_stop_hook(*, codex_dir: Path, backup_dir: Path) -> None:
    """Delete the `hooks.Stop` array from ~/.codex/hooks.json (other events kept)."""
    hooks = codex_dir / "hooks.json"
    if not hooks.exists():
        return
    backup_file(hooks, backup_dir=backup_dir)

    data = json.loads(hooks.read_text(encoding="utf-8"))
    if "hooks" in data and "Stop" in data["hooks"]:
        del data["hooks"]["Stop"]
    _atomic_write(hooks, json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# launchd plist install
# ---------------------------------------------------------------------------


def install_launchd_mirror(
    *,
    template_path: Path,
    launch_dir: Path,
    memoryd_bin: str,
    data_root: str,
) -> Path:
    """Render the plist template into `<launch_dir>/com.memoryd.mirror.plist`.

    Returns the rendered path. Caller is responsible for `launchctl bootstrap`.
    """
    launch_dir.mkdir(parents=True, exist_ok=True)
    template = template_path.read_text(encoding="utf-8")
    rendered = template.replace("__MEMORYD_BIN__", memoryd_bin).replace(
        "__MEMORYD_DATA_ROOT__", data_root
    )
    out = launch_dir / "com.memoryd.mirror.plist"
    _atomic_write(out, rendered)
    return out


def uninstall_launchd_mirror(*, launch_dir: Path) -> bool:
    """Delete the plist. Returns True if a file was deleted."""
    out = launch_dir / "com.memoryd.mirror.plist"
    if out.exists():
        out.unlink()
        return True
    return False
```

- [ ] **Step 5：在 `memoryd/src/memoryd/cli.py` 加 setup 子命令**

加导入（如未有）：

```python
from . import setup as setup_mod
```

把 main() 函数的 subparser 注册段加：

```python
    p_setup = subs.add_parser(
        "setup",
        help="manage user-side config wire-up (~/.codex/, ~/Library/LaunchAgents/)",
    )
    setup_subs = p_setup.add_subparsers(dest="setup_cmd", required=True)

    # swap-codex-notify
    p_swap = setup_subs.add_parser("swap-codex-notify", help="swap Codex notify between probe/wrapper/original")
    p_swap.add_argument("--to", choices=["probe", "wrapper", "original"], required=True)
    p_swap.add_argument("--codex-dir", default=str(Path.home() / ".codex"))
    p_swap.add_argument("--backup-dir", default=str(Path.home() / ".claude" / "backups"))
    p_swap.add_argument("--probe-path", default="/Users/abble/project-management-personal/scripts/codex-notify-probe.sh")
    p_swap.add_argument("--wrapper-path", default="/Users/abble/project-management-personal/scripts/codex-notify-wrapper.sh")
    p_swap.set_defaults(func=_cmd_swap_notify)

    # remove-codex-stop-hook
    p_rm = setup_subs.add_parser("remove-codex-stop-hook", help="drop the dead Stop entry from ~/.codex/hooks.json")
    p_rm.add_argument("--codex-dir", default=str(Path.home() / ".codex"))
    p_rm.add_argument("--backup-dir", default=str(Path.home() / ".claude" / "backups"))
    p_rm.set_defaults(func=_cmd_remove_stop_hook)

    # install-launchd-mirror
    p_inst = setup_subs.add_parser("install-launchd-mirror", help="render and install LaunchAgent plist")
    p_inst.add_argument("--template", default="/Users/abble/project-management-personal/scripts/launchd/com.memoryd.mirror.plist")
    p_inst.add_argument("--launch-dir", default=str(Path.home() / "Library" / "LaunchAgents"))
    p_inst.add_argument("--memoryd-bin", default="/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd")
    p_inst.add_argument("--data-root", default=str(Path.home() / ".local" / "share" / "memoryd"))
    p_inst.set_defaults(func=_cmd_install_launchd)

    # uninstall-launchd-mirror
    p_un = setup_subs.add_parser("uninstall-launchd-mirror")
    p_un.add_argument("--launch-dir", default=str(Path.home() / "Library" / "LaunchAgents"))
    p_un.set_defaults(func=_cmd_uninstall_launchd)
```

并在文件中 `cmd_capture` / `cmd_mirror` 旁加 wrapper 函数：

```python
def _cmd_swap_notify(args: argparse.Namespace) -> int:
    setup_mod.swap_codex_notify(
        to=args.to,
        codex_dir=Path(args.codex_dir),
        backup_dir=Path(args.backup_dir),
        probe_path=args.probe_path,
        wrapper_path=args.wrapper_path,
    )
    print(f"swap-codex-notify: notify swapped to {args.to}", file=sys.stderr)
    return 0


def _cmd_remove_stop_hook(args: argparse.Namespace) -> int:
    setup_mod.remove_codex_stop_hook(
        codex_dir=Path(args.codex_dir),
        backup_dir=Path(args.backup_dir),
    )
    print("remove-codex-stop-hook: ok", file=sys.stderr)
    return 0


def _cmd_install_launchd(args: argparse.Namespace) -> int:
    out = setup_mod.install_launchd_mirror(
        template_path=Path(args.template),
        launch_dir=Path(args.launch_dir),
        memoryd_bin=args.memoryd_bin,
        data_root=args.data_root,
    )
    print(f"install-launchd-mirror: rendered to {out}", file=sys.stderr)
    print(
        "next step: launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.memoryd.mirror.plist",
        file=sys.stderr,
    )
    return 0


def _cmd_uninstall_launchd(args: argparse.Namespace) -> int:
    deleted = setup_mod.uninstall_launchd_mirror(launch_dir=Path(args.launch_dir))
    print(f"uninstall-launchd-mirror: {'deleted' if deleted else 'not installed'}", file=sys.stderr)
    return 0
```

- [ ] **Step 6：跑测试通过**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_setup.py -v`
Expected: 7 passed.

跑全量：

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest -v`
Expected: 54 + 7 = 61 passed.

- [ ] **Step 7：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/setup.py memoryd/src/memoryd/cli.py memoryd/tests/test_setup.py scripts/launchd/com.memoryd.mirror.plist
git commit -m "$(cat <<'EOF'
加 memoryd setup CLI + launchd plist 模板

子命令：
- swap-codex-notify --to probe|wrapper|original（自动备份 + 用 tomllib
  read，正则替换 notify 字段，保留其他 keys）
- remove-codex-stop-hook（删 Stop 子树，保其他 hook 事件）
- install-launchd-mirror（占位符渲染 plist 到 ~/Library/LaunchAgents/）
- uninstall-launchd-mirror

所有改动文件都 backup 到 ~/.claude/backups/，原子写。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8：清理 + README + execution log + Phase 1 用户手册

**Files:**
- Delete: `scripts/codex-stop-hook.sh`
- Modify: `memoryd/README.md`
- Create: `docs/superpowers/plans/2026-05-14-tri-client-capture-fix.execution-log.txt`

- [ ] **Step 1：删除死代码 codex-stop-hook.sh**

```bash
cd /Users/abble/project-management-personal
git rm scripts/codex-stop-hook.sh
```

- [ ] **Step 2：重写 README 的 Codex / OpenClaw 章节**

读当前 `memoryd/README.md`（已存在 Plan 2 版本），把 "Wire into Codex" 整章替换为：

````markdown
## Wire into Codex（Plan 2.5 双通路）

> Codex.app 的 hooks engine 当前版本对所有事件零触发（已实测）；Plan 2.5
> 改走两条互补通路：notify wrapper 实时捕获 + 文件系统监听 rollout_summary。
> 旧的 `scripts/codex-stop-hook.sh` 已删除。

### 1. 备份并替换 notify 字段（实时通路）

```bash
# 完整切到 wrapper（先做一遍 probe 才知道 notify 真实 schema；
# 详见下面 Phase 1 手册）
/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd setup swap-codex-notify --to wrapper
```

子命令自动：
- 把 `~/.codex/config.toml` 备份到 `~/.claude/backups/`
- 用 Python tomllib 读，正则替换 `notify` 字段保留其他 keys
- 把原 notify target 存到 `~/.codex/.memoryd-notify-state.json`，便于 `--to original` 回滚

### 2. 删除死的 Stop hook 条目

```bash
memoryd setup remove-codex-stop-hook
```

### 3. 启动 FS-watch daemon（事后通路）

```bash
memoryd setup install-launchd-mirror
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.memoryd.mirror.plist
launchctl print gui/$(id -u)/com.memoryd.mirror  # 验证 daemon 在跑
```

Daemon 监听 `~/.codex/memories/rollout_summaries/`；Codex.app 每个 session 结束后
会自己往那里写一份 summary `.md`，daemon 把它转码成 memoryd 的 `source=codex-rollout`
记忆条目。

### 4. 验证

跑一轮 Codex.app turn，检查：

```bash
# 实时通路日志
tail ~/.local/share/memoryd/logs/codex-notify.log

# FS-watch 通路日志
tail ~/.local/share/memoryd/logs/mirror.stderr.log

# 新生成的 memoryd 条目
find ~/.local/share/memoryd/scopes -name "*.md" -newer /tmp -ls
```
````

替换 OpenClaw 章节为：

````markdown
## Wire into OpenClaw（Plan 2.5 双通路）

> OpenClaw 2026.5.7 的插件 SDK 是 `definePluginEntry` + `registerAgentEventSubscription`，
> 不再支持旧的 `api.on('agent_end', ...)`。Plan 2.5 重写插件入口；同时让 launchd
> daemon 监听 `~/.openclaw/agents/*/sessions/` 作 fallback。

### 1. 安装插件

```bash
cd /Users/abble/project-management-personal/scripts/openclaw-memoryd-plugin
openclaw plugins install --force .
openclaw plugins list | grep memoryd-openclaw
```

### 2. 授权对话访问

```bash
# 用 install 输出的 entry key（通常就是 memoryd-openclaw）
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowConversationAccess true
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowPromptInjection false
```

### 3. FS-watch daemon

Codex 那一步装的 launchd plist 已经同时覆盖 OpenClaw 路径（`--codex --openclaw` 双开）；无需额外操作。

### 4. 验证

跑一轮 OpenClaw turn，检查：

```bash
tail ~/.local/share/memoryd/logs/openclaw-events.log  # SDK 通路诊断
find ~/.local/share/memoryd/scopes -newer /tmp -name "*.md" -ls
```

`source: openclaw`（SDK 实时）或 `source: openclaw-fs`（FS-watch）。
````

- [ ] **Step 3：写 execution log + Phase 1 用户手册**

```bash
cat > /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-14-tri-client-capture-fix.execution-log.txt <<'LOG'
=== Plan 2.5 实施日志 ===

[Phase 0 自动化任务由 subagent 完成；时间戳由各 commit 给出]

=== Phase 1 用户手册（按顺序执行）===

# 0. 准备
cd /Users/abble/project-management-personal
git pull --ff-only main  # 确保拿到 Phase 0 所有 commit
cd memoryd && uv sync && cd ..

# 1. Codex notify 探针（探一次 Codex.app 实际给 notify 什么）
memoryd/.venv/bin/memoryd setup swap-codex-notify --to probe
# >>> 打开 Codex.app，开 1 个新会话，发任意 prompt（如"hi"），让它产 1 个 turn
# >>> 然后立刻：
cat ~/.local/share/memoryd/probe/notify-probe.log
# 把整段日志粘给我（subagent），并立刻：
memoryd/.venv/bin/memoryd setup swap-codex-notify --to original
# 这一步至少把 SkyComputerUseClient 切回原样；后面我看完探针日志再决定要不要 swap 到 wrapper

# 2. OpenClaw 插件安装 + 授权
cd scripts/openclaw-memoryd-plugin
openclaw plugins install --force .
openclaw plugins list | grep memoryd-openclaw
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowConversationAccess true
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowPromptInjection false
cd ../..

# 3. launchd daemon 起
memoryd/.venv/bin/memoryd setup install-launchd-mirror
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.memoryd.mirror.plist
sleep 2
launchctl print gui/$(id -u)/com.memoryd.mirror | head -30
# 看 state = running

# 4. 删 Codex 死 hook 条目
memoryd/.venv/bin/memoryd setup remove-codex-stop-hook

# 5. tri-client e2e 暗号验证
TEST_PROJECT="$HOME/tmp/memoryd-tri-e2e-plan2.5-$(date +%s)"
mkdir -p "$TEST_PROJECT" && cd "$TEST_PROJECT" && git init -q
SCOPE_HASH=$(python3 -c "
import sys
sys.path.insert(0, '/Users/abble/project-management-personal/memoryd/src')
from memoryd.scope import scope_hash, resolve_scope_root
from pathlib import Path
print(scope_hash(resolve_scope_root(Path('$TEST_PROJECT'))))
")
echo "scope_hash: $SCOPE_HASH"

# 5a. CC 写第一条 (CC-WATERMELON-7777)
claude   # 在 TUI 里说："请记住：暗号 CC-WATERMELON-7777"，然后 /exit
sleep 3
ls "$HOME/.local/share/memoryd/scopes/$SCOPE_HASH/sessions/" | grep "$(date +%Y-%m-%d)"

# 5b. Codex 写第二条 (CODEX-PEAR-3333)
open -a "Codex"  # 或你日常启动方式
# 在 Codex.app 里说："cd $TEST_PROJECT，请记住：暗号 CODEX-PEAR-3333"，然后 /exit
sleep 30  # rollout_summary 写盘需要一点时间
grep -rln "PEAR-3333" "$HOME/.local/share/memoryd/scopes/$SCOPE_HASH/" || \
  grep -rln "PEAR-3333" "$HOME/.local/share/memoryd/scopes/_unscoped/"

# 5c. OpenClaw 写第三条 (OPENCLAW-MANGO-4242)
cd "$TEST_PROJECT" && openclaw  # 在 TUI 里说："请记住：暗号 OPENCLAW-MANGO-4242"，然后退出
sleep 5
grep -rln "MANGO-4242" "$HOME/.local/share/memoryd/scopes/$SCOPE_HASH/" || \
  grep -rln "MANGO-4242" "$HOME/.local/share/memoryd/scopes/_unscoped/"

# 5d. 三端跨召回
cd "$TEST_PROJECT"
claude
# 说："用 search_memory 找 PEAR 和 MANGO 的暗号"
# 期望：两个都召回

codex  # 或 Codex.app
# 说："用 memoryd 找 WATERMELON 和 MANGO"

openclaw
# 说："用 memoryd 找 WATERMELON 和 PEAR"

# 6. 把结果回报
echo "[$(date -Iseconds)] tri-client e2e RESULT: <PASS/FAIL+症状>" \
    >> /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-14-tri-client-capture-fix.execution-log.txt

=== Phase 1 手册 end ===
LOG
```

- [ ] **Step 4：跑全量回归测试**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest -v`
Expected: 61 passed.

Run: `cd /Users/abble/project-management-personal/scripts/openclaw-memoryd-plugin && npm test`
Expected: `tests 12 pass 12 fail 0`.

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/README.md docs/superpowers/plans/2026-05-14-tri-client-capture-fix.execution-log.txt
git commit -m "$(cat <<'EOF'
Plan 2.5 收尾：删死代码、重写 README Codex/OpenClaw 章节、execution log

- 删 scripts/codex-stop-hook.sh（Codex hooks engine 实测零触发，死代码）
- README 改成 Plan 2.5 双通路文档（notify wrapper + FS-watch / SDK + FS-watch）
- execution log 含 Phase 1 用户操作手册（探针 → 插件 → daemon → tri-client e2e）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Plan 2.5 完成判据

下面任一未达成即未完成：

1. ✅ pytest 61 passed（含 4 mirror 框架 + 7 mirror_codex + 7 mirror_openclaw + 2 cli mirror + 7 setup + Plan 1/2 的 34）
2. ✅ node `--test` 12 passed（7 payload + 5 register）
3. ✅ `~/.codex/config.toml` 经 `memoryd setup swap-codex-notify --to wrapper` 后 notify 指向 wrapper，原 mcp_servers / projects / shell_environment_policy / plugins 全部保留（用 `python3 -c "import tomllib; ..."` 验）
4. ✅ `~/.codex/hooks.json` Stop 条目移除，其他事件（若有）保留
5. ✅ `~/Library/LaunchAgents/com.memoryd.mirror.plist` 存在且 plutil -lint 通过
6. ✅ `launchctl bootstrap` 起后 daemon `state = running`
7. ✅ OpenClaw `plugins list` 含 `memoryd-openclaw`
8. ✅ tri-client e2e 6 个跨端召回全 PASS（CC ↔ Codex ↔ OpenClaw 互相召回另两端的暗号）
9. ✅ Plan 1 / Plan 2 已有功能无回归（CC SessionEnd hook 仍写入；memoryd MCP `search_memory` 工具仍可用）

## Plan 间依赖

**上游：** Plan 1（commit `2e7fb25`）、Plan 2（commit `5a61f43`）已 merge；本 plan 替换 Plan 2 的 Codex stop hook + OpenClaw mjs 插件实现，不动 schema / storage / search / MCP server。

**下游：**
- **Plan 3（长期治理）**：依赖本 plan 引入的 5 种 source tag；fingerprint-based cross-path merging 在 SQLite 索引后实现
- **Plan 5（跨平台）**：本 plan 的 launchd plist 是 macOS-only；Plan 5 给 Linux 加 systemd timer、给 Windows 加 Task Scheduler
- **Plan 7（浏览界面）**：Web Dashboard 需要展示 source tag 颜色 / 过滤

---

## Self-Review

Spec → plan 映射：

- spec §1 硬事实清单 → plan 顶部 "Codex rollout_summary 真实格式" + "风险与不确定性" 已覆盖
- spec §2 架构图 → Task 4（mirror CLI）+ Task 5（notify wrapper）+ Task 6（SDK 插件）+ Task 7（setup + launchd）
- spec §3 source tag 策略 → Task 2/3/6 frontmatter source 字段 + plan 顶部 Architecture 段
- spec §4 scope 反推 → Task 2 直接读 cwd 字段 + Task 3 reverse_lookup_scope_from_content
- spec §5 文件清单 → 本 plan "文件结构" 表
- spec §6 phasing → Phase 0（Task 1-8）+ Task 8 execution log 内的 Phase 1 手册
- spec §7 完成判据 → 本 plan "Plan 2.5 完成判据" 9 项
- spec §8 边界 → Task 注释里说明（"Plan 3 LLM-summarizes", "Plan 5 cross-platform"）
- spec §9 风险与回退 → plan "风险与不确定性" + Task 5/7 的 `--to original` 路径
- spec §11 变更记录 → 本 plan commit 序列

Placeholder scan：

- 无 "TBD" / "TODO" / "实现细节后补"
- 每个 step 都附完整 code block
- 每个测试 expected 都给数字 / 字符串

Type 一致性：

- `MirrorRouter.dispatch(path)` ↔ `FileHandler = Callable[[Path], None]` ↔ Task 2/3 handler 类的 `__call__(self, path: Path) -> None` 签名一致
- `save_to_scope_or_unscoped(memory_root, session, *, resolved_scope_hash)` 在 Task 1/2/3 三处调用方式一致
- `makeHandler({ spawn, log })` ↔ `makeSubscription({ id, spawn, log })` 字段名 spawn / log 在 register.js 和 register.test.mjs 一致
- `_PATH_PATTERN` 命名 / `_parse_iso` 私有 helper 命名 — 单文件内一致即可

通过。Plan 完成。
