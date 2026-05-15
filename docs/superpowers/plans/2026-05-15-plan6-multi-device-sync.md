# 多电脑同步（Plan 6）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task.

**Goal:** spec §4.7 #26 SessionEnd 自动 export / SessionStart 自动 import 多电脑同步落地——裸 .md 镜像到用户自配同步盘；fingerprint 增量 dedup；冲突进 `_conflicts/`；passphrase-derived 模式让敏感作用域 .md.enc 跨设备可解。

**Architecture:** `sync.py` 实现 export/import/status；增量基于 SQLite `memories.fingerprint` + sync dir `.memoryd-sync-state.json`。SQLite/audit/grants/logs **不**进同步盘。`passphrase.py` 包 OS keyring 存 master passphrase；`enc.py` 按 `[sensitive] key_source` 在 random（Plan 4 default）和 PBKDF2-derived 之间分发。

**Tech Stack:** Python 3.11+；既有 `cryptography>=42`（用其 PBKDF2HMAC）。spec: `docs/superpowers/specs/2026-05-15-plan6-multi-device-sync-design.md`。

**Decomposition Note:** 8 plan 中的第 6 个。上游 Plan 1-5 全 merged（79b533c）。下游 Plan 7 Web Dashboard、Plan 8 旧记忆导入。

---

## 文件结构

| 路径 | 责任 | 操作 |
|---|---|---|
| `memoryd/src/memoryd/sync.py` | export / import / status 主逻辑 | Create |
| `memoryd/src/memoryd/passphrase.py` | master passphrase 读写（OS keyring） | Create |
| `memoryd/src/memoryd/config.py` | 加 SyncConfig + SensitiveConfig | Modify |
| `memoryd/src/memoryd/enc.py` | key_source 分发 random vs passphrase | Modify |
| `memoryd/src/memoryd/cli.py` | sync export/import/status + set-passphrase 子命令 | Modify |
| `memoryd/src/memoryd/capture.py` 或 cli.py capture 入口 | auto_import pre-step | Modify |
| `scripts/cc-session-end-hook.py` / `.ps1` | 末尾 fork sync export（if enabled） | Modify |
| `memoryd/tests/test_sync_export.py` | export 增量 + skip 黑名单 + dry-run | Create |
| `memoryd/tests/test_sync_import.py` | import + auto-rebuild-index | Create |
| `memoryd/tests/test_sync_conflicts.py` | 冲突落 `_conflicts/` + audit | Create |
| `memoryd/tests/test_sync_status.py` | status JSON 输出 | Create |
| `memoryd/tests/test_passphrase.py` | set/get/env override | Create |
| `memoryd/tests/test_enc_passphrase.py` | PBKDF2 roundtrip + 跨进程一致性 | Create |
| `memoryd/README.md` | 加 Plan 6 章节 | Modify |
| `docs/superpowers/plans/2026-05-15-plan6-multi-device-sync.execution-log.txt` | Phase 1 真机手册 | Create |

---

## 风险与不确定性

1. **sync dir 路径里有 `~`**：必须 `Path(cfg.sync.dir).expanduser().resolve()` 后再用。Task 1 加该 helper。
2. **跨平台同步盘路径差异**：macOS 是 `~/Library/CloudStorage/Dropbox/...`，Linux 是 `~/Dropbox/...`，Win 是 `~/Dropbox/`。用户在 config.toml 显式配，memoryd 不猜。
3. **passphrase 长度检查**：≥ 12 字符；不强制 entropy 复杂度（用户自负）。env 覆盖路径必须支持空字符串识别为未设置。
4. **跨机器 scope_hash 差异**：用户路径不同 → scope_hash 不同。Plan 6 不解决，README + execution-log 明确说明。
5. **黑名单**：sync export 必须排除 `index.db`、`audit/`、`grants/`、`logs/`、`probe/`。Task 1 用一个常量列表。
6. **WAL 锁**：SQLite 即使我们不主动同步 `.db`，同步盘软件可能仍 hook 整个 data root。建议用户的同步盘 root 配在 `~/Library/CloudStorage/.../memoryd-sync/`（独立目录，不覆盖 `~/.local/share/memoryd/`）。文档说明。
7. **auto_import 雪崩**：同一秒多个 CC capture → 多个 import fork。用 5 分钟节流文件（`~/.local/share/memoryd/last_import_at`），同一进程组只首个真跑。

---

## Task 1：sync helpers + config + scope-state manifest

**Files:**
- Create: `memoryd/src/memoryd/sync.py`（先框架 + helpers）
- Modify: `memoryd/src/memoryd/config.py`（加 SyncConfig + SensitiveConfig）
- Create: `memoryd/tests/test_sync_export.py`（仅 helper 测试，import logic 后续 task）

加：
- `SyncConfig(enabled, dir, auto_export_on_session_end, auto_import_on_session_start)`
- `SensitiveConfig(key_source, kdf_iters)`
- `Config` dict subclass（同 Plan 5 Task 2）已存在 `.notify`；加 `.sync` 和 `.sensitive`
- `_load_sync(data)` / `_load_sensitive(data)` 函数（按现有 `_load_notify` 风格）

`sync.py` helpers：
```python
"""Multi-device sync: raw .md mirror to user-configured sync dir."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import load_config

log = logging.getLogger(__name__)

# Files / dirs that must NEVER enter sync dir
_SYNC_BLACKLIST_NAMES = {"index.db", "index.db-wal", "index.db-shm"}
_SYNC_BLACKLIST_DIRS = {"audit", "grants", "logs", "probe"}
_STATE_FILENAME = ".memoryd-sync-state.json"


def expand_sync_dir(raw: str) -> Path:
    """Expand ~ and env vars; return absolute resolved Path."""
    return Path(raw).expanduser().resolve()


def iter_local_markdown(data_root: Path) -> Iterable[Path]:
    """Yield every .md / .md.enc / .memoryd-sensitive under scopes/, skipping blacklist."""
    scopes = data_root / "scopes"
    if not scopes.exists():
        return
    for path in scopes.rglob("*"):
        if not path.is_file():
            continue
        if path.name in _SYNC_BLACKLIST_NAMES:
            continue
        if any(part in _SYNC_BLACKLIST_DIRS for part in path.parts):
            continue
        if path.suffix in {".md", ".enc"} or path.name.endswith(".md.enc") or path.name == ".memoryd-sensitive":
            yield path


def read_state(sync_dir: Path) -> dict:
    f = sync_dir / _STATE_FILENAME
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text("utf-8"))
    except Exception:
        log.warning("corrupt sync state; ignoring")
        return {}


def write_state(sync_dir: Path, state: dict) -> None:
    sync_dir.mkdir(parents=True, exist_ok=True)
    (sync_dir / _STATE_FILENAME).write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True),
        "utf-8",
    )


def relative_key(data_root: Path, path: Path) -> str:
    """Stable key for state manifest: scope_hash/type/slug.ext."""
    return str(path.relative_to(data_root / "scopes")).replace("\\", "/")
```

测试：
- `test_expand_sync_dir`: `~/foo` → `/Users/.../foo`
- `test_iter_local_markdown_skips_blacklist`: 创建 `index.db` / `audit/audit.jsonl` / `logs/x.log` / `scopes/h/sessions/a.md` → only the .md 出现
- `test_read_write_state_roundtrip`
- `test_relative_key_uses_forward_slash`
- `test_load_sync_config_defaults`（config）

### Steps

- [ ] Step 1: 写 sync.py helpers
- [ ] Step 2: config.py 加 SyncConfig + SensitiveConfig + _load_sync + _load_sensitive
- [ ] Step 3: 写 test_sync_export.py 5 个 helper 测试 + 1 个 config 测试（加进 test_config.py）
- [ ] Step 4: `cd memoryd && uv run pytest tests/test_sync_export.py tests/test_config.py -v`
- [ ] Step 5: 全套 `cd memoryd && uv run pytest 2>&1 | tail -5`，预期 ~205 passed
- [ ] Step 6: commit `plan6/task1: sync helpers + SyncConfig + SensitiveConfig`

---

## Task 2：sync export

**Files:**
- Modify: `memoryd/src/memoryd/sync.py`
- Create: `memoryd/tests/test_sync_export.py` 增量补 export 实测

主体函数：

```python
@dataclass
class ExportReport:
    copied: int = 0
    skipped: int = 0
    dry_run: bool = False
    files: list[str] = None

    def __post_init__(self):
        if self.files is None:
            self.files = []


def export(
    data_root: Path,
    sync_dir: Path,
    *,
    scope_hash: str | None = None,
    dry_run: bool = False,
) -> ExportReport:
    """Mirror local markdown to sync dir; incremental via fingerprint state."""
    state = read_state(sync_dir)
    new_state = dict(state)
    report = ExportReport(dry_run=dry_run)
    for src in iter_local_markdown(data_root):
        key = relative_key(data_root, src)
        if scope_hash and not key.startswith(scope_hash + "/"):
            continue
        fp = _fingerprint(src)
        if state.get(key) == fp:
            report.skipped += 1
            continue
        dst = sync_dir / "scopes" / key
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
        new_state[key] = fp
        report.copied += 1
        report.files.append(key)
    if not dry_run:
        write_state(sync_dir, new_state)
    return report


def _fingerprint(path: Path) -> str:
    """sha256 of file bytes; cheap, deterministic, no SQLite round-trip needed."""
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
```

测试场景：
- `test_export_copies_new_md`：本地有 1 个 .md，sync dir 空 → copied=1
- `test_export_skips_unchanged`：本地 1 个，sync state 已是同 fp → copied=0 skipped=1
- `test_export_skips_blacklist`：包含 index.db / audit / logs → 不出现在 sync
- `test_export_dry_run_writes_nothing`：dry_run=True → 文件不存在
- `test_export_filters_by_scope`：scope_hash=A → 只动 A 下
- `test_export_state_file_persisted`

### Steps

- [ ] Step 1: 实现 export + _fingerprint
- [ ] Step 2: 补 6 个测试
- [ ] Step 3: 跑 + 全套 (~211 passed)
- [ ] Step 4: commit `plan6/task2: sync export 增量 + state manifest`

---

## Task 3：sync import + conflict detection

**Files:**
- Modify: `memoryd/src/memoryd/sync.py`
- Create: `memoryd/tests/test_sync_import.py`
- Create: `memoryd/tests/test_sync_conflicts.py`

主体函数：

```python
@dataclass
class ImportReport:
    copied: int = 0
    skipped: int = 0
    conflicts: int = 0
    dry_run: bool = False


def import_(
    data_root: Path,
    sync_dir: Path,
    *,
    scope_hash: str | None = None,
    dry_run: bool = False,
) -> ImportReport:
    """Pull from sync dir to local; resolve conflicts into _conflicts/<slug>-<fp8>.md."""
    report = ImportReport(dry_run=dry_run)
    sync_scopes = sync_dir / "scopes"
    if not sync_scopes.exists():
        return report
    for src in sync_scopes.rglob("*"):
        if not src.is_file():
            continue
        if src.name in _SYNC_BLACKLIST_NAMES:
            continue
        if not (src.suffix in {".md"} or src.name.endswith(".md.enc") or src.name == ".memoryd-sensitive"):
            continue
        rel = src.relative_to(sync_scopes)
        key = str(rel).replace("\\", "/")
        if scope_hash and not key.startswith(scope_hash + "/"):
            continue
        local = data_root / "scopes" / rel
        if not local.exists():
            if not dry_run:
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(src.read_bytes())
            report.copied += 1
            continue
        if _fingerprint(local) == _fingerprint(src):
            report.skipped += 1
            continue
        # conflict
        if not dry_run:
            local_fp = _fingerprint(local)[:8]
            conflicts_dir = data_root / "scopes" / "_conflicts"
            conflicts_dir.mkdir(parents=True, exist_ok=True)
            backup = conflicts_dir / f"{rel.name}-{local_fp}"
            backup.write_bytes(local.read_bytes())
            local.write_bytes(src.read_bytes())
        report.conflicts += 1
    if not dry_run:
        _rebuild_index_quiet(data_root)
    return report


def _rebuild_index_quiet(data_root: Path) -> None:
    """Best-effort rebuild_index; never raise."""
    try:
        from .index import rebuild_index
        rebuild_index(data_root)
    except Exception as e:
        log.warning("post-import rebuild_index failed: %s", e)
```

测试：
- `test_import_copies_new_files`
- `test_import_skips_identical`
- `test_import_writes_conflict_for_diverged_slug`：本地和 sync 都有同 slug 不同内容 → 本地版进 `_conflicts/<slug>-<fp8>`，sync 版覆盖 local
- `test_import_dry_run_writes_nothing`
- `test_import_filters_by_scope`
- `test_import_triggers_rebuild_index`：mock rebuild_index 检查被调

### Steps

- [ ] Step 1: 实现 import_ + _rebuild_index_quiet
- [ ] Step 2: 补 12 个测试
- [ ] Step 3: 跑 + 全套（~223 passed）
- [ ] Step 4: commit `plan6/task3: sync import + 冲突隔离 + 自动 rebuild-index`

---

## Task 4：sync status + CLI

**Files:**
- Modify: `memoryd/src/memoryd/sync.py`
- Modify: `memoryd/src/memoryd/cli.py`
- Create: `memoryd/tests/test_sync_status.py`

```python
def status(data_root: Path, sync_dir: Path) -> dict:
    """Return per-scope counts and timestamps."""
    state = read_state(sync_dir)
    per_scope: dict[str, dict[str, int]] = {}
    for p in iter_local_markdown(data_root):
        h = p.relative_to(data_root / "scopes").parts[0]
        per_scope.setdefault(h, {"local": 0, "sync": 0})["local"] += 1
    if (sync_dir / "scopes").exists():
        for p in (sync_dir / "scopes").rglob("*"):
            if not p.is_file(): continue
            if p.name == _STATE_FILENAME: continue
            if not (p.suffix in {".md"} or p.name.endswith(".md.enc")
                    or p.name == ".memoryd-sensitive"):
                continue
            h = p.relative_to(sync_dir / "scopes").parts[0]
            per_scope.setdefault(h, {"local": 0, "sync": 0})["sync"] += 1
    conflicts = 0
    cdir = data_root / "scopes" / "_conflicts"
    if cdir.exists():
        conflicts = sum(1 for _ in cdir.iterdir())
    return {
        "sync_dir": str(sync_dir),
        "state_entries": len(state),
        "per_scope": per_scope,
        "conflicts": conflicts,
    }
```

CLI:

```python
# sync export / import / status
p_sync = subparsers.add_parser("sync", help="multi-device sync (raw .md mirror)")
sync_subs = p_sync.add_subparsers(dest="sync_cmd", required=True)
p_exp = sync_subs.add_parser("export"); p_exp.add_argument("--scope"); p_exp.add_argument("--dry-run", action="store_true")
p_imp = sync_subs.add_parser("import"); p_imp.add_argument("--scope"); p_imp.add_argument("--dry-run", action="store_true")
p_sta = sync_subs.add_parser("status"); p_sta.add_argument("--json", action="store_true")
p_exp.set_defaults(func=_cmd_sync_export)
p_imp.set_defaults(func=_cmd_sync_import)
p_sta.set_defaults(func=_cmd_sync_status)
```

dispatch fn 用 load_config + expand_sync_dir + report 打印。

测试：
- `test_status_lists_scopes_and_counts`
- `test_status_counts_conflicts`
- `test_cli_sync_export_dry_run`（subprocess invoke 或 args.func 直调）
- `test_cli_sync_import_dry_run`
- `test_cli_sync_status_json`

### Steps

- [ ] Step 1: 实现 status
- [ ] Step 2: cli.py wire-up
- [ ] Step 3: 5 测试
- [ ] Step 4: 全套 ~228 passed
- [ ] Step 5: commit `plan6/task4: sync status + CLI 子命令`

---

## Task 5：passphrase + enc.py 双 mode

**Files:**
- Create: `memoryd/src/memoryd/passphrase.py`
- Modify: `memoryd/src/memoryd/enc.py`
- Modify: `memoryd/src/memoryd/cli.py`
- Create: `memoryd/tests/test_passphrase.py`
- Create: `memoryd/tests/test_enc_passphrase.py`

```python
# passphrase.py
"""Master passphrase: env-overridable, OS keyring backed."""
from __future__ import annotations

import os
import sys
from typing import Final

_SERVICE: Final = "memoryd-master-passphrase"
_ACCOUNT: Final = "default"
_ENV: Final = "MEMORYD_MASTER_PASSPHRASE"
_MIN_LEN: Final = 12


class PassphraseError(Exception): ...


def get() -> bytes | None:
    env = os.environ.get(_ENV)
    if env:
        return env.encode("utf-8")
    import keyring
    try:
        v = keyring.get_password(_SERVICE, _ACCOUNT)
    except Exception:
        return None
    return v.encode("utf-8") if v else None


def set_(passphrase: str) -> None:
    if len(passphrase) < _MIN_LEN:
        raise PassphraseError(f"need ≥ {_MIN_LEN} characters")
    import keyring
    keyring.set_password(_SERVICE, _ACCOUNT, passphrase)


def clear() -> None:
    import keyring
    try:
        keyring.delete_password(_SERVICE, _ACCOUNT)
    except Exception:
        pass
```

`enc.py` 改造：

```python
def get_or_create_scope_key(scope_hash: str) -> bytes:
    _check_backend_available()
    from .config import load_config
    cfg = load_config()
    if getattr(cfg, "sensitive", None) and cfg.sensitive.key_source == "passphrase":
        return _get_passphrase_scope_key(scope_hash, cfg.sensitive.kdf_iters)
    return _get_random_scope_key(scope_hash)


def _get_passphrase_scope_key(scope_hash: str, iters: int) -> bytes:
    from . import passphrase as pp
    p = pp.get()
    if not p:
        raise EncError("master passphrase unset; run `memoryd set-passphrase`")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=scope_hash.encode("utf-8"), iterations=iters)
    return kdf.derive(p)


def _get_random_scope_key(scope_hash: str) -> bytes:
    # 原 Plan 4 实现
    ...
```

CLI:

```python
p_pp = subparsers.add_parser("set-passphrase", help="set memoryd master passphrase (sensitive scope cross-device)")
p_pp.set_defaults(func=_cmd_set_passphrase)

def _cmd_set_passphrase(args):
    import getpass
    p1 = getpass.getpass("Master passphrase: ")
    p2 = getpass.getpass("Confirm: ")
    if p1 != p2:
        print("mismatch", file=sys.stderr); return 1
    try:
        from . import passphrase
        passphrase.set_(p1)
    except passphrase.PassphraseError as e:
        print(str(e), file=sys.stderr); return 1
    print("master passphrase stored locally", file=sys.stderr)
    return 0
```

测试：
- `test_get_returns_env_when_set`
- `test_get_returns_keyring_value_when_env_unset`
- `test_set_rejects_too_short`
- `test_set_stores_in_keyring`
- `test_enc_passphrase_derivation_deterministic`：同 scope_hash + 同 passphrase → 同 key
- `test_enc_passphrase_diff_scope_different_key`
- `test_enc_passphrase_raises_when_unset`
- `test_enc_random_mode_unaffected_by_passphrase_config`

### Steps

- [ ] Step 1-2: 实现 passphrase.py + enc.py 分发
- [ ] Step 3: cli.py wire-up
- [ ] Step 4: 8 测试
- [ ] Step 5: 全套 ~236 passed
- [ ] Step 6: commit `plan6/task5: passphrase-derived 密钥 opt-in + set-passphrase CLI`

---

## Task 6：SessionEnd / capture auto-sync 集成

**Files:**
- Modify: `scripts/cc-session-end-hook.py` / `.ps1`
- Modify: `memoryd/src/memoryd/cli.py`（capture 入口）

scripts/cc-session-end-hook.py 加末尾：

```python
def _fork_sync_export():
    try:
        from memoryd.config import load_config
        cfg = load_config()
        if not (cfg.sync.enabled and cfg.sync.auto_export_on_session_end):
            return
    except Exception:
        return
    memoryd_bin = shutil.which("memoryd") or "memoryd"
    subprocess.Popen(
        [memoryd_bin, "sync", "export"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
```

调在 `main` 末尾 `_fork_sync_export()`。

capture 入口（cli.py cmd_capture 开头）：

```python
def _maybe_auto_import():
    try:
        from .config import load_config
        cfg = load_config()
        if not (cfg.sync.enabled and cfg.sync.auto_import_on_session_start):
            return
    except Exception:
        return
    marker = Path.home() / ".local" / "share" / "memoryd" / "last_import_at"
    if marker.exists():
        try:
            import time
            if time.time() - marker.stat().st_mtime < 300:
                return
        except Exception:
            pass
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    memoryd_bin = shutil.which("memoryd") or sys.executable
    subprocess.Popen(
        [memoryd_bin, "sync", "import"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
```

`cmd_capture` 首行 `_maybe_auto_import()`。

测试：
- `test_capture_no_auto_import_when_disabled`
- `test_capture_forks_import_when_enabled`（mock subprocess.Popen 检查被调）
- `test_capture_throttles_within_5_min`（mock marker mtime）

### Steps

- [ ] Step 1: 改 cc-session-end-hook.py 加 _fork_sync_export
- [ ] Step 2: 同步改 .ps1（PowerShell 写一个 if cfg-check + Start-Process）
- [ ] Step 3: capture 入口加 _maybe_auto_import
- [ ] Step 4: 3 测试
- [ ] Step 5: 全套 ~239 passed
- [ ] Step 6: commit `plan6/task6: SessionEnd auto-export + capture auto-import 节流`

---

## Task 7：README + execution log + 收尾

**Files:**
- Modify: `memoryd/README.md`
- Create: `docs/superpowers/plans/2026-05-15-plan6-multi-device-sync.execution-log.txt`

README 加 "## Multi-device sync (Plan 6)" 章节：

- 配置 [sync] / [sensitive] toml
- sync export / import / status 命令
- set-passphrase 流程
- 跨平台 scope_hash 一致性 caveat
- _conflicts/ 桶语义

execution-log Phase 1：

- 在 Mac 上 cd memoryd/scope/<some>，跑 sync export，看 sync dir 镜像生成
- 模拟"换机"：删除本地 .md（保留 sync dir）→ sync import → 看 .md 回来
- mark-sensitive 一个 scope，passphrase 模式，在测试 dir 解密 .md.enc

完成判据校验：
- pytest ≥ 239 passed
- openclaw plugin 12 passed 无回归
- README + execution-log 写完
- finishing-a-development-branch 自动 PR + merge

### Steps

- [ ] Step 1: README 加 Plan 6 章节 + Status 升 v0.6.0
- [ ] Step 2: 写 execution-log
- [ ] Step 3: 跑全套 + node test
- [ ] Step 4: commit `plan6/task7: README + execution log`
- [ ] Step 5: finishing-a-development-branch
