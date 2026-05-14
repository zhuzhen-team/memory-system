# 敏感作用域（Plan 4）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** spec §4.5 一整套敏感作用域：`mark-sensitive` 标 scope → macOS Keychain AES-256-GCM 加密 `.md → .md.enc` → MCP 工具调用前必有效 grant → JSONL append-only audit（prev_hash chain 防篡改） → 8/12 MCP 工具。

**Architecture:** scope_sensitive 列 + `.memoryd-sensitive` marker 文件做 scope 标志；`enc.py` 包 Keychain key 管理 + AES-256-GCM；`storage.save_memory` 检测 sensitive scope 走 `.md.enc`；`governance/gate.py` 每个 MCP 工具调用前调 `check_or_raise(scope_hash)`；`grants/<hash>.json` 存授权 token（once/session/task）；`audit/audit.jsonl` 每行带 prev_hash 链。

**Tech Stack:** Python 3.11+；新增依赖 `keyring>=24`（Keychain）+ `cryptography>=42`（AES-GCM）。spec: `docs/superpowers/specs/2026-05-14-sensitive-scopes-design.md` (`f74fd2c`)。

**Decomposition Note:** 8 plan 中的第 4 个。上游 Plan 1/2/2.5/3 已 merge（`4dac127`）。下游 Plan 5 加 Win DPAPI/Linux Secret Service；Plan 6 处理多电脑密钥同步；Plan 7 audit web UI。

---

## 文件结构

| 路径 | 责任 | 操作 |
|---|---|---|
| `memoryd/pyproject.toml` | 加 `keyring>=24`, `cryptography>=42` | Modify |
| `memoryd/src/memoryd/migrations/002_sensitive_scope.sql` | `ALTER TABLE memories ADD COLUMN scope_sensitive` | Create |
| `memoryd/src/memoryd/scope_meta.py` | `.memoryd-sensitive` 文件读写 + sensitive 父目录探测 | Create |
| `memoryd/src/memoryd/enc.py` | Keychain key + AES-256-GCM encrypt/decrypt | Create |
| `memoryd/src/memoryd/storage.py` | save_memory / load_session 集成加密 | Modify |
| `memoryd/src/memoryd/governance/grants.py` | grant token 读写 + 过期检查 | Create |
| `memoryd/src/memoryd/governance/gate.py` | check_or_raise + interactive prompt + audit append | Create |
| `memoryd/src/memoryd/governance/audit.py` | append + prev_hash chain + 查询 + verify | Create |
| `memoryd/src/memoryd/server.py` | 每个工具体首行调 gate.check_or_raise + 新工具 request_sensitive_read | Modify |
| `memoryd/src/memoryd/cli.py` | 加 mark-sensitive / unmark-sensitive / grant / revoke / audit 子命令 | Modify |
| `memoryd/tests/test_scope_meta.py` | sensitive 父目录探测测试 | Create |
| `memoryd/tests/test_enc.py` | encrypt/decrypt roundtrip + Keychain mock | Create |
| `memoryd/tests/test_storage_sensitive.py` | save sensitive 写 .md.enc / load 透明解密 | Create |
| `memoryd/tests/test_grants.py` | grant token 写读 + once/session/task expiry | Create |
| `memoryd/tests/test_gate.py` | check_or_raise + AuthorizationRequired | Create |
| `memoryd/tests/test_audit.py` | append + prev_hash chain + verify | Create |
| `memoryd/tests/test_cli_sensitive.py` | mark/unmark/grant/revoke/audit CLI 集成 | Create |
| `memoryd/tests/test_server_sensitive.py` | server 工具 gate 阻塞 / 放行 + request_sensitive_read | Create |
| `memoryd/README.md` | 加 Sensitive scopes 章节 | Modify |
| `docs/superpowers/plans/2026-05-14-sensitive-scopes.execution-log.txt` | Phase 1 用户手册（mark / grant 实测） | Create |

---

## 风险与不确定性

1. **Keychain access prompt**：第一次 `security add-generic-password` 会让 macOS 弹"允许访问钥匙串"系统对话框；subagent 自动化测试用 `keyring` 时绕过这个（在 `~/Library/Keychains/` 临时建测试 keychain）。生产场景用户首次 mark-sensitive 会看到一次系统弹框。
2. **`keyring` PyPI 包**：macOS 后端是 `macOS_Keychain`，调 `security` CLI；可用，但慢（每次 ~100ms）。对 mark-sensitive / 启动时拿 key 是一次性成本，可接受。
3. **`.md.enc` 文件名**：Plan 2.5 mirror module 用 `path.suffix.lower() != ".md"` early return；本 plan 要让 mirror 也认 `.md.enc`，否则 sensitive scope 的 Codex/OpenClaw 镜像就丢了。**Task 3 storage 改造时同步改 mirror 文件后缀检查**（一行 patch）。
4. **`/dev/tty` 不可用**：GUI client 没 controlling tty。interactive prompt 静默 fallback 到抛 `AuthorizationRequired`（Task 6 实现），不阻塞。
5. **audit prev_hash 链**：首行 prev_hash 用全零；本 plan 不实现 verify 命令（推迟到 Plan 7 audit UI），但写入逻辑保证链是可验的。
6. **encrypt 后 SQLite index 怎么办**：spec §4 明确——metadata（title/triggers/scope/type）保持明文进 SQLite；只 body 加密。Plan 4 整 .md 文件加密；但 `load_session` 先解密 → parse frontmatter → 喂 index_memory 拿 frontmatter 字段。这意味着 `rebuild-index` 在 sensitive scope 上需要 Keychain 解锁；本 plan 接受这个代价。

---

## Task 1：SQLite migration 002 + scope_meta 模块

**Files:**
- Create: `memoryd/src/memoryd/migrations/002_sensitive_scope.sql`
- Create: `memoryd/src/memoryd/scope_meta.py`
- Create: `memoryd/tests/test_scope_meta.py`

加 `scope_sensitive` 列；scope_meta 模块负责 `.memoryd-sensitive` marker 文件读写 + 向上遍历探测。

### Migration SQL `002_sensitive_scope.sql`

```sql
ALTER TABLE memories ADD COLUMN scope_sensitive INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_memories_sensitive ON memories (scope_sensitive);
```

注意：现有 `index.py` `_run_migrations` 用 `for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):` + `conn.executescript(sql)` —— 反复跑也无害？ALTER TABLE ADD COLUMN 在 SQLite 是非幂等的（第二次跑会 error "duplicate column name"）。**改 `_run_migrations`**：建一个 `_schema_migrations` 表记录已跑文件名，跳过已跑的。这是 Task 1 顺手的改造。

### `scope_meta.py`

```python
"""Sensitive scope marker management.

`.memoryd-sensitive` is a plain text file at the scope root that signals
"this directory tree is sensitive". Children inherit unconditionally.
"""
from __future__ import annotations

from pathlib import Path


MARKER_FILENAME = ".memoryd-sensitive"


def find_sensitive_root(path: Path) -> Path | None:
    """Walk parents from `path`; return the first directory that contains
    `.memoryd-sensitive`. None if no ancestor is sensitive."""
    cur = Path(path).resolve()
    for ancestor in [cur, *cur.parents]:
        if (ancestor / MARKER_FILENAME).exists():
            return ancestor
    return None


def is_path_sensitive(path: Path) -> bool:
    return find_sensitive_root(path) is not None


def mark_sensitive(scope_root: Path) -> Path:
    """Create .memoryd-sensitive at scope_root. Errors if a parent is already sensitive."""
    scope_root = scope_root.resolve()
    existing = find_sensitive_root(scope_root)
    if existing is not None and existing != scope_root:
        raise ValueError(f"parent already sensitive: {existing}")
    marker = scope_root / MARKER_FILENAME
    marker.write_text(f"scope_root: {scope_root}\n", encoding="utf-8")
    return marker


def unmark_sensitive(scope_root: Path) -> None:
    """Remove .memoryd-sensitive. No-op if not present."""
    marker = Path(scope_root).resolve() / MARKER_FILENAME
    if marker.exists():
        marker.unlink()
```

### Tests `test_scope_meta.py`

```python
"""scope_meta tests."""
from pathlib import Path

import pytest

from memoryd.scope_meta import (
    MARKER_FILENAME,
    find_sensitive_root,
    is_path_sensitive,
    mark_sensitive,
    unmark_sensitive,
)


def test_find_returns_none_when_no_marker(tmp_path: Path):
    assert find_sensitive_root(tmp_path) is None


def test_find_returns_self_when_marker_at_path(tmp_path: Path):
    (tmp_path / MARKER_FILENAME).write_text("x")
    assert find_sensitive_root(tmp_path) == tmp_path.resolve()


def test_find_returns_ancestor_when_marker_above(tmp_path: Path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (tmp_path / MARKER_FILENAME).write_text("x")
    assert find_sensitive_root(deep) == tmp_path.resolve()


def test_is_path_sensitive_true_when_ancestor_marked(tmp_path: Path):
    (tmp_path / MARKER_FILENAME).write_text("x")
    assert is_path_sensitive(tmp_path / "sub")


def test_mark_sensitive_writes_marker_with_scope_root(tmp_path: Path):
    p = mark_sensitive(tmp_path)
    assert p.exists()
    assert "scope_root:" in p.read_text()


def test_mark_sensitive_refuses_when_parent_already_sensitive(tmp_path: Path):
    (tmp_path / MARKER_FILENAME).write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    with pytest.raises(ValueError, match="parent already sensitive"):
        mark_sensitive(sub)


def test_unmark_sensitive_removes_marker(tmp_path: Path):
    (tmp_path / MARKER_FILENAME).write_text("x")
    unmark_sensitive(tmp_path)
    assert not (tmp_path / MARKER_FILENAME).exists()


def test_unmark_sensitive_noop_when_missing(tmp_path: Path):
    unmark_sensitive(tmp_path)  # should not raise
```

### Migration 防重复跑改造（index.py）

```python
def _run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _schema_migrations (filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {r[0] for r in conn.execute("SELECT filename FROM _schema_migrations").fetchall()}
    for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name in applied:
            continue
        sql = sql_file.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO _schema_migrations (filename, applied_at) VALUES (?, datetime('now'))",
            (sql_file.name,),
        )
    conn.commit()
```

### TDD 步骤

- [ ] Step 1：写测试（上面 8 个）
- [ ] Step 2：失败
- [ ] Step 3：写 migration .sql + scope_meta.py + 改 index.py `_run_migrations`
- [ ] Step 4：跑 `cd memoryd && uv run pytest tests/test_scope_meta.py tests/test_index.py -v` 期望 17 passed（scope_meta 8 + index 9 都通）
- [ ] Step 5：全量 117 + 8 = 125 passed
- [ ] Step 6：commit

```bash
git add memoryd/src/memoryd/migrations/002_sensitive_scope.sql memoryd/src/memoryd/scope_meta.py memoryd/src/memoryd/index.py memoryd/tests/test_scope_meta.py
git commit -m "$(cat <<'EOF'
加 scope_meta + SQLite migration 002 + 幂等 migration runner

- scope_meta.py: .memoryd-sensitive marker 文件读写 + 向上遍历探测
- migration 002: ALTER memories ADD COLUMN scope_sensitive
- 改 _run_migrations 用 _schema_migrations 元表记跑过的，避免重复跑 ALTER

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2：加密模块（enc.py + Keychain 集成）

**Files:**
- Modify: `memoryd/pyproject.toml`（加 `keyring>=24`, `cryptography>=42`）
- Create: `memoryd/src/memoryd/enc.py`
- Create: `memoryd/tests/test_enc.py`

### `enc.py`

```python
"""macOS Keychain-backed AES-256-GCM encryption for sensitive memories.

Per scope_hash → 32-byte AES key stored in macOS Keychain via `keyring`.
File format: base64(nonce[12] || ciphertext || tag[16]).
Associated_data = scope_hash (prevents ciphertext from being swapped
between scopes).
"""
from __future__ import annotations

import base64
import os
import secrets
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


SERVICE: Final = "memoryd-scope-key"


class EncError(Exception):
    """Raised when encryption / decryption / key access fails."""


def _keyring():
    try:
        import keyring
    except ImportError as e:
        raise EncError("keyring SDK not installed") from e
    return keyring


def get_or_create_scope_key(scope_hash: str) -> bytes:
    kr = _keyring()
    existing = kr.get_password(SERVICE, scope_hash)
    if existing:
        try:
            return base64.b64decode(existing)
        except Exception as e:
            raise EncError(f"corrupt key for {scope_hash}") from e
    key = secrets.token_bytes(32)
    kr.set_password(SERVICE, scope_hash, base64.b64encode(key).decode())
    return key


def delete_scope_key(scope_hash: str) -> None:
    kr = _keyring()
    try:
        kr.delete_password(SERVICE, scope_hash)
    except Exception:
        pass  # best-effort


def encrypt_bytes(scope_hash: str, plaintext: bytes) -> bytes:
    key = get_or_create_scope_key(scope_hash)
    aes = AESGCM(key)
    nonce = secrets.token_bytes(12)
    ct = aes.encrypt(nonce, plaintext, scope_hash.encode())
    return base64.b64encode(nonce + ct)


def decrypt_bytes(scope_hash: str, blob: bytes) -> bytes:
    key = get_or_create_scope_key(scope_hash)
    raw = base64.b64decode(blob)
    nonce, ct = raw[:12], raw[12:]
    aes = AESGCM(key)
    return aes.decrypt(nonce, ct, scope_hash.encode())
```

### Tests `test_enc.py`

测试用 monkeypatch 替换 `_keyring()` 返回 in-memory dict 模拟 Keychain。

```python
"""enc.py tests with in-memory keyring stub."""
from typing import Any

import pytest

from memoryd import enc
from memoryd.enc import (
    EncError,
    decrypt_bytes,
    delete_scope_key,
    encrypt_bytes,
    get_or_create_scope_key,
)


class _InMemKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.store[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        self.store.pop((service, account), None)


@pytest.fixture(autouse=True)
def stub_keyring(monkeypatch):
    fake = _InMemKeyring()
    monkeypatch.setattr(enc, "_keyring", lambda: fake)
    return fake


def test_get_or_create_creates_new_key(stub_keyring):
    k = get_or_create_scope_key("scope1")
    assert len(k) == 32
    assert ("memoryd-scope-key", "scope1") in stub_keyring.store


def test_get_or_create_returns_existing_key(stub_keyring):
    k1 = get_or_create_scope_key("scope1")
    k2 = get_or_create_scope_key("scope1")
    assert k1 == k2


def test_encrypt_decrypt_roundtrip():
    pt = b"hello sensitive content"
    blob = encrypt_bytes("scope1", pt)
    assert pt not in blob  # actually encrypted
    out = decrypt_bytes("scope1", blob)
    assert out == pt


def test_decrypt_rejects_wrong_scope_hash():
    """Associated_data binding prevents cross-scope ciphertext reuse."""
    blob = encrypt_bytes("scope1", b"secret")
    with pytest.raises(Exception):
        decrypt_bytes("scope2", blob)


def test_delete_scope_key(stub_keyring):
    get_or_create_scope_key("s")
    delete_scope_key("s")
    assert ("memoryd-scope-key", "s") not in stub_keyring.store


def test_encrypt_different_nonces_each_call():
    """Two encryptions of the same plaintext produce different ciphertexts."""
    a = encrypt_bytes("scope1", b"same input")
    b = encrypt_bytes("scope1", b"same input")
    assert a != b
```

### 添加依赖

`pyproject.toml`：

```toml
dependencies = [
    "mcp[cli]>=1.0.0",
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "watchdog>=4.0",
    "anthropic>=0.40",
    "keyring>=24",
    "cryptography>=42",
]
```

### TDD 步骤

- [ ] Step 1：pyproject 加依赖；`uv sync`
- [ ] Step 2：写测试
- [ ] Step 3：失败
- [ ] Step 4：实现 enc.py
- [ ] Step 5：跑测试期望 6 passed；全量 125 + 6 = 131 passed
- [ ] Step 6：commit

```bash
git add memoryd/pyproject.toml memoryd/uv.lock memoryd/src/memoryd/enc.py memoryd/tests/test_enc.py
git commit -m "$(cat <<'EOF'
加 enc.py：macOS Keychain + AES-256-GCM 加密层

- keyring>=24 + cryptography>=42 依赖
- get_or_create_scope_key 取/创 32-byte key 存 Keychain
- encrypt/decrypt_bytes 用 AES-GCM；nonce 每次随机；associated_data
  = scope_hash 防 cross-scope ciphertext 复用

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3：Storage 集成加密（save / load 自动加解密 + scope_sensitive 列联动）

**Files:**
- Modify: `memoryd/src/memoryd/storage.py`
- Modify: `memoryd/src/memoryd/mirror.py`（让 router 也认 `.md.enc`）
- Create: `memoryd/tests/test_storage_sensitive.py`

### 关键改动

`save_memory`：
- 检测 scope_root 是否 sensitive（用 scope_meta.find_sensitive_root + path = resolve_scope_root(...)）
- 若是 → 编码 `to_markdown()` → enc.encrypt_bytes → 写 `<slug>.md.enc`（**不是 .md**）
- body_path 记录 `.md.enc`
- index 时 `scope_sensitive=1`

`load_session`：
- path 结尾 `.md.enc` → 先 decrypt → 然后正常 parse

`mirror.MirrorRouter.dispatch`：suffix 检查支持 `.md` 和 `.md.enc`（Plan 2.5 mirror 通路对 sensitive scope 仍工作）。改 `path.suffix.lower()` 为 `_normalized_suffix(path)` 把 `.md.enc` 规范化成 `.md`，handler 拿到 `.md.enc` path 自己解 / 调 load_session。

实际上 mirror 在 Plan 2.5 是把 Codex / OpenClaw 的原文件转码成 memoryd .md；它自己写 .md 时也要走 save_memory（已经在 mirror_codex / mirror_openclaw 调），所以 save 路径已经走加密分支。**mirror_codex 输入文件是 Codex 的 plain .md（rollout_summary）；这个文件不在 sensitive scope 里（在 ~/.codex/memories/），不需要加密**。只有 mirror **写入** memoryd 的 .md 在 sensitive scope 时需要加密——这个 save_memory 已经处理。

所以 Task 3 不动 mirror.py，只动 storage。

### Tests `test_storage_sensitive.py`

```python
"""storage.py sensitive-scope encryption tests."""
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import load_session, save_memory


def _make_mem(scope_root: Path, slug: str = "2026-05-14-x") -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug=slug,
            type="session",
            scope_hash="h_sensitive",  # not derived; explicit for test
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body="SECRET CONTENT",
    )


@pytest.fixture
def sensitive_scope_root(tmp_path: Path):
    """Make tmp_path a sensitive scope by dropping the marker."""
    (tmp_path / ".memoryd-sensitive").write_text("scope_root: " + str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def stub_keyring(monkeypatch):
    """Reuse in-memory keyring from enc tests."""
    from tests.test_enc import _InMemKeyring  # noqa: F401
    from memoryd import enc
    fake = _InMemKeyring()
    monkeypatch.setattr(enc, "_keyring", lambda: fake)


def test_save_memory_writes_enc_file_in_sensitive_scope(
    sensitive_scope_root: Path, monkeypatch
):
    """When scope is sensitive, `.md.enc` file appears (not `.md`)."""
    monkeypatch.setattr(
        "memoryd.storage._resolve_scope_root_for_save",
        lambda mem: sensitive_scope_root,
    )
    mem = _make_mem(sensitive_scope_root)
    data_root = sensitive_scope_root / ".data"
    data_root.mkdir()
    path = save_memory(data_root, mem)
    assert path.suffix == ".enc"
    assert path.parent.name == "sessions"
    # plaintext not on disk:
    assert b"SECRET CONTENT" not in path.read_bytes()


def test_load_session_decrypts_enc_file(sensitive_scope_root: Path, monkeypatch):
    monkeypatch.setattr(
        "memoryd.storage._resolve_scope_root_for_save",
        lambda mem: sensitive_scope_root,
    )
    mem = _make_mem(sensitive_scope_root)
    data_root = sensitive_scope_root / ".data"
    data_root.mkdir()
    path = save_memory(data_root, mem)
    loaded = load_session(path)
    assert "SECRET CONTENT" in loaded.body


def test_save_memory_writes_plain_md_in_nonsensitive_scope(tmp_path: Path):
    """No `.memoryd-sensitive` ancestor → plain `.md`."""
    mem = _make_mem(tmp_path, slug="ns")
    mem = mem.model_copy(update={
        "frontmatter": mem.frontmatter.model_copy(update={"scope_hash": "non_sensitive"})
    })
    path = save_memory(tmp_path, mem)
    assert path.suffix == ".md"
    assert ".enc" not in str(path)


def test_index_marks_scope_sensitive_column(
    sensitive_scope_root: Path, monkeypatch
):
    monkeypatch.setattr(
        "memoryd.storage._resolve_scope_root_for_save",
        lambda mem: sensitive_scope_root,
    )
    mem = _make_mem(sensitive_scope_root)
    data_root = sensitive_scope_root / ".data"
    data_root.mkdir()
    save_memory(data_root, mem)
    idx = open_index(data_root / "index.db")
    row = idx.get_memory(mem.frontmatter.slug)
    idx.close()
    assert row["scope_sensitive"] == 1
```

注意：`_resolve_scope_root_for_save(mem)` 是 storage 内部 helper 用来从 SessionMemory 推断 scope root（mem 只有 scope_hash 没有 root path）。简化：在 Plan 4 storage 改造里把 `mark_sensitive` 时记下 scope_hash → scope_root 映射到 SQLite `memories.scope_sensitive` 列 + 一个新表 `sensitive_scopes(scope_hash, scope_root)`。或者更简单：让 save_memory 接受一个 optional `scope_root` 参数 caller 给出（capture / mirror 都有 scope_root）；caller 不给则 fallback 用 SQLite 查 sensitive_scopes 表。

实际上更简的做法：scope_meta 文件存在 `<memoryd-data-root>/scopes/<scope_hash>/.memoryd-sensitive`（memoryd 内部 marker），而**不**是用户原始 scope_root 的 .memoryd-sensitive。

但 spec 写的是 "用户标了目录"——也就是放在用户项目根，不是 memoryd 数据根。让我们维持 spec：用户在 `~/scopes/finance/.memoryd-sensitive`，memoryd 通过 SQLite 表 `sensitive_scopes(scope_hash, scope_root, marked_at)` 缓存映射。`mark-sensitive` 写两个地方：用户目录 marker 文件 + SQLite 表。`storage.save_memory` 查 SQLite 表知道 scope_hash 是否敏感。

### 改造 `_run_migrations`（Plan 4 增加 003）

新加 `003_sensitive_scopes_table.sql`：

```sql
CREATE TABLE IF NOT EXISTS sensitive_scopes (
  scope_hash TEXT PRIMARY KEY,
  scope_root TEXT NOT NULL,
  marked_at  TEXT NOT NULL
);
```

`Index` 加 helper：

```python
def is_scope_sensitive(self, scope_hash: str) -> bool:
    row = self.conn.execute(
        "SELECT 1 FROM sensitive_scopes WHERE scope_hash = ?", (scope_hash,)
    ).fetchone()
    return row is not None

def register_sensitive_scope(self, scope_hash: str, scope_root: str) -> None: ...
def unregister_sensitive_scope(self, scope_hash: str) -> None: ...
def list_sensitive_scopes(self) -> list[dict]: ...
```

### `storage.save_memory` 改造

```python
def save_memory(root: Path, mem: SessionMemory) -> Path:
    _validate_slug(mem.frontmatter.slug)
    target_dir = _type_dir(root, mem.frontmatter.scope_hash, mem.frontmatter.type)
    target_dir.mkdir(parents=True, exist_ok=True)

    idx = open_index(root / "index.db")
    try:
        sensitive = idx.is_scope_sensitive(mem.frontmatter.scope_hash)
    finally:
        # keep idx open for index_memory later; reopen pattern unchanged
        pass

    if sensitive:
        from .enc import encrypt_bytes
        plaintext = mem.to_markdown().encode("utf-8")
        blob = encrypt_bytes(mem.frontmatter.scope_hash, plaintext)
        path = target_dir / f"{mem.frontmatter.slug}.md.enc"
        path.write_bytes(blob)
    else:
        path = target_dir / f"{mem.frontmatter.slug}.md"
        path.write_text(mem.to_markdown(), encoding="utf-8")

    body_rel = str(path.relative_to(root))
    try:
        idx.index_memory(mem, body_path=body_rel)
        if sensitive:
            idx.conn.execute(
                "UPDATE memories SET scope_sensitive = 1 WHERE slug = ?",
                (mem.frontmatter.slug,),
            )
            idx.conn.commit()
    finally:
        idx.close()
    return path
```

### `load_session` 改造

```python
def load_session(path: Path) -> SessionMemory:
    if path.suffix == ".enc" and path.name.endswith(".md.enc"):
        from .enc import decrypt_bytes
        # need scope_hash to decrypt — read from index
        idx = open_index(path.parent.parent.parent.parent / "index.db")
        try:
            row = idx.conn.execute(
                "SELECT scope_hash FROM memories WHERE body_path LIKE ?",
                (f"%{path.name}",),
            ).fetchone()
        finally:
            idx.close()
        if row is None:
            raise FileNotFoundError(f"no SQLite index row for {path}")
        plaintext = decrypt_bytes(row[0], path.read_bytes()).decode("utf-8")
        return SessionMemory.from_markdown(plaintext)
    text = path.read_text(encoding="utf-8")
    return SessionMemory.from_markdown(text)
```

注意：`path.parent.parent.parent.parent` 的层数取决于 `<root>/scopes/<hash>/<type>/<slug>.md.enc` —— 4 层 parent 到 root。但 caller 经常已经传绝对 path；最干净是给 `load_session` 加 optional `scope_hash` 参数（caller 给出避免一遍 SQLite 查询）。**简化：把 `load_session` 改成 `load_session(path, *, memory_root=None)`，当 memory_root 给出时直接用，不给出则按目录上溯找 `index.db`。**

### TDD 步骤

- [ ] Step 1：写 migration 003 + index helper + tests
- [ ] Step 2：跑测试看现有 Plan 1-3 测试是否还过（应该过；scope_sensitive 默认 0）
- [ ] Step 3：写 sensitive 测试
- [ ] Step 4：实现 storage 改造
- [ ] Step 5：跑测试期望全过；131 + 4 = 135 passed
- [ ] Step 6：commit

---

## Task 4：mark/unmark-sensitive CLI

**Files:**
- Modify: `memoryd/src/memoryd/cli.py`
- Create: `memoryd/tests/test_cli_sensitive.py`（部分）

`mark-sensitive`：
1. resolve scope_root
2. check 父目录无 marker
3. scope_meta.mark_sensitive 写 marker 文件
4. idx.register_sensitive_scope(scope_hash, scope_root)
5. 遍历 `<root>/scopes/<scope_hash>/**/*.md` → 读 → encrypt_bytes → 写 .md.enc → 删原 .md
6. SQLite memories.scope_sensitive=1 for that scope
7. audit "sensitive_marked"

`unmark-sensitive` 逆操作。

测试用 monkeypatch keyring + tmp_path。

### TDD 步骤

- [ ] Step 1：写测试（mark + unmark roundtrip 验证 .md → .md.enc → .md 还原）
- [ ] Step 2-4：实现 + 修测试通过
- [ ] Step 5：commit

---

## Task 5：grants 模块 + grant/revoke CLI

**Files:**
- Create: `memoryd/src/memoryd/governance/grants.py`
- Create: `memoryd/tests/test_grants.py`
- Modify: `memoryd/src/memoryd/cli.py`（加 grant / revoke 子命令）

`grants.py`：

```python
def grant_path(scope_hash: str) -> Path: ...
def write_grant(scope_hash, scope_root, duration, task_id=None) -> dict: ...
def read_grant(scope_hash) -> dict | None: ...
def is_grant_valid(grant: dict, *, now: datetime | None = None) -> bool: ...
def revoke_grant(scope_hash, task_id=None) -> bool: ...
```

duration → expires_at:
- `once`: now + 90s
- `session`: now + 8h
- `task`: 9999-12-31

测试：roundtrip + 过期 + revoke + task 过滤。

### TDD 步骤

- [ ] Step 1：写 5 测试
- [ ] Step 2-5：实现 + CLI 注册 + commit

---

## Task 6：gate 模块 + audit 模块

**Files:**
- Create: `memoryd/src/memoryd/governance/gate.py`
- Create: `memoryd/src/memoryd/governance/audit.py`
- Create: `memoryd/tests/test_gate.py`
- Create: `memoryd/tests/test_audit.py`

`audit.py`：

```python
def audit_log_path() -> Path: ...
def _current_prev_hash() -> str: ...  # sha256 of last line, or zeros
def append_event(event: dict) -> None: ...  # writes JSONL with prev_hash
def query_events(*, scope_hash=None, since=None, event_type=None) -> list[dict]: ...
def verify_chain() -> tuple[bool, int]: ...  # 返回 (链完整, 第一条破损行号)
```

`gate.py`：

```python
class AuthorizationRequired(Exception):
    """Raised by gate when a sensitive scope access lacks a valid grant."""

def check_or_raise(scope_hash: str, tool: str) -> None:
    """Read grant; raise if no valid grant. Audit access_granted/access_denied."""

def interactive_prompt(scope_root: str) -> str | None:
    """If MEMORYD_AUTH_INTERACTIVE=1 + /dev/tty available, prompt user;
    return chosen duration ('once' / 'session' / 'task') or None on decline."""
```

测试：
- audit append + prev_hash 链
- query + since 过滤
- gate check no-grant → AuthorizationRequired
- gate check valid grant → 不 raise
- interactive_prompt no-tty → None

### TDD 步骤

- [ ] Step 1：写 ~10 测试
- [ ] Step 2-5：实现 + commit

---

## Task 7：server.py 加 gate 拦截 + request_sensitive_read 工具

**Files:**
- Modify: `memoryd/src/memoryd/server.py`
- Create: `memoryd/tests/test_server_sensitive.py`

每个工具函数体首行调 `gate.check_or_raise(sh, "<tool_name>")`（在 `_default_scope()` 解析后立刻调）。

新增 `request_sensitive_read(scope_path, query, duration="once")` 工具——智能体显式请求授权；内部调 interactive_prompt 或 raise AuthorizationRequired。

测试：
- search_memory on sensitive scope w/o grant → tool error
- search_memory on sensitive scope w/ valid grant → returns hits
- request_sensitive_read → writes grant when interactive accepted
- request_sensitive_read no-tty → AuthorizationRequired

### TDD 步骤

- [ ] Step 1-5：标准 TDD + commit

---

## Task 8：audit CLI + README + execution log

**Files:**
- Modify: `memoryd/src/memoryd/cli.py`（加 audit 子命令）
- Modify: `memoryd/README.md`
- Create: `docs/superpowers/plans/2026-05-14-sensitive-scopes.execution-log.txt`

`memoryd audit [--scope=X] [--since=ISO] [--event-type=Y] [--json]`：调 audit.query_events，输出表格或 JSON。

README 加 "Sensitive scopes" 章节：mark-sensitive 工作流 / grant duration 语义 / audit 查询示例 / MEMORYD_AUTH_INTERACTIVE 说明。

execution-log：Phase 1 用户手册（mark 一个测试 scope → 验加密 → 试 search 拿 AuthorizationRequired → grant 后再 search 成功 → audit 查看事件流）。

跑全量回归 145 passed。

### TDD 步骤

- [ ] Step 1：写 audit CLI + 1 测试
- [ ] Step 2：写 README 段
- [ ] Step 3：写 execution log
- [ ] Step 4：全量回归
- [ ] Step 5：commit

---

## Plan 4 完成判据

1. ✅ pytest 全绿（117 + 新增 ~28 = ~145）
2. ✅ `memoryd mark-sensitive <path>` → marker 文件 + Keychain key + .md → .md.enc
3. ✅ 标后 search_memory 在没 grant 时 raise AuthorizationRequired
4. ✅ `memoryd grant --duration once|session|task` 写 grant token；search_memory 通过
5. ✅ `request_sensitive_read` 工具可用（8/12 MCP budget）
6. ✅ `memoryd audit` 输出含 access_granted / access_denied / grant_issued / sensitive_marked 等事件
7. ✅ audit.jsonl 末尾 prev_hash chain 可校验
8. ✅ Plan 1-3 已有功能无回归

## Plan 间依赖

- 上游：Plan 1/2/2.5/3 merged on main `4dac127`
- 下游：Plan 5 加 Win DPAPI / Linux Secret Service → `enc.py` 后端切换；Plan 6 解决跨设备密钥同步；Plan 7 audit Web UI
