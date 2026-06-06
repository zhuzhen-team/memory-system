"""Tests for ``memoryd doctor`` health check.

These tests are hermetic — they never touch the real ``~/.claude``,
``~/.codex``, or the user's data root. Each individual check function is
exercised across its three states (OK / WARN / FAIL where applicable).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memoryd import doctor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_db(data_root: Path, *, sessions: int = 0, entities: int = 0,
             sessions_recent: int = 0, profile_versions: int = 0) -> Path:
    """Create a minimal index.db schema with the columns the doctor reads."""
    data_root.mkdir(parents=True, exist_ok=True)
    db = data_root / "index.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS memories (
            slug TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            scope_hash TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE IF NOT EXISTS profile_versions (
            version_num INTEGER PRIMARY KEY,
            written_at TEXT
        );
        """
    )
    now = datetime.now(timezone.utc)
    for i in range(sessions):
        # Stagger ages: first ``sessions_recent`` are within the last day,
        # the rest are 30 days old (outside the 7-day window).
        if i < sessions_recent:
            created = now - timedelta(hours=2 + i)
        else:
            created = now - timedelta(days=30)
        conn.execute(
            "INSERT INTO memories(slug, type, scope_hash, created_at) VALUES(?,?,?,?)",
            (f"slug-{i}", "session", "scopeX", created.isoformat()),
        )
    for i in range(entities):
        conn.execute(
            "INSERT INTO entities(id, name) VALUES(?,?)",
            (f"entity:{i}", f"name-{i}"),
        )
    for i in range(1, profile_versions + 1):
        conn.execute(
            "INSERT INTO profile_versions(version_num, written_at) VALUES(?,?)",
            (i, now.isoformat()),
        )
    conn.commit()
    conn.close()
    return db


def _write_cc_settings(path: Path, *, session_start: str | None = None,
                      session_end: str | None = None) -> None:
    """Build a minimal ``~/.claude/settings.json`` with the requested hooks."""
    data: dict = {"hooks": {}}
    if session_start is not None:
        data["hooks"]["SessionStart"] = [{
            "matcher": "*",
            "hooks": [{"type": "command", "command": session_start}],
        }]
    if session_end is not None:
        data["hooks"]["SessionEnd"] = [{
            "matcher": "*",
            "hooks": [{"type": "command", "command": session_end}],
        }]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# check_binary / check_python_version
# ---------------------------------------------------------------------------


def test_binary_ok_when_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/usr/local/bin/memoryd")
    r = doctor.check_binary()
    assert r.status == "ok"
    assert "memoryd" in r.value


def test_binary_fail_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    monkeypatch.setattr(doctor.sys, "argv", ["/some/other/program"])
    r = doctor.check_binary()
    assert r.status == "fail"
    assert r.hint is not None


def test_python_version_ok() -> None:
    r = doctor.check_python_version()
    # CI always runs >=3.11, so this should be OK.
    assert r.status == "ok"


# ---------------------------------------------------------------------------
# check_data_root
# ---------------------------------------------------------------------------


def test_data_root_fail_when_missing(tmp_path: Path) -> None:
    r = doctor.check_data_root(tmp_path / "nope")
    assert r.status == "fail"


def test_data_root_info_when_db_missing(tmp_path: Path) -> None:
    """Fresh install (dir exists but no db yet) is INFO, not WARN.

    Previously WARN, which scared new users into thinking the install was
    broken when actually they just hadn't captured anything yet. The db is
    lazily created on first capture; nothing to fix.
    """
    (tmp_path / "data").mkdir()
    r = doctor.check_data_root(tmp_path / "data")
    assert r.status == "info"


def test_data_root_ok_when_db_opens(tmp_path: Path) -> None:
    _seed_db(tmp_path / "data")
    r = doctor.check_data_root(tmp_path / "data")
    assert r.status == "ok"


# ---------------------------------------------------------------------------
# check_memory_counts
# ---------------------------------------------------------------------------


def test_memory_counts_warn_when_empty(tmp_path: Path) -> None:
    _seed_db(tmp_path / "data")
    r = doctor.check_memory_counts(tmp_path / "data")
    assert r.status == "warn"
    assert "session:0" in r.value


def test_memory_counts_ok_with_data(tmp_path: Path) -> None:
    _seed_db(tmp_path / "data", sessions=3)
    r = doctor.check_memory_counts(tmp_path / "data")
    assert r.status == "ok"
    assert "session:3" in r.value


# ---------------------------------------------------------------------------
# check_entities (KG)
# ---------------------------------------------------------------------------


def test_entities_warn_when_sessions_but_no_entities(tmp_path: Path) -> None:
    _seed_db(tmp_path / "data", sessions=10)
    r = doctor.check_entities(tmp_path / "data")
    assert r.status == "warn"
    assert "analyze-session" in (r.hint or "")


def test_entities_ok_when_populated(tmp_path: Path) -> None:
    _seed_db(tmp_path / "data", sessions=3, entities=5)
    r = doctor.check_entities(tmp_path / "data")
    assert r.status == "ok"
    assert "5 entities" in r.value


def test_entities_info_when_no_sessions(tmp_path: Path) -> None:
    _seed_db(tmp_path / "data")
    r = doctor.check_entities(tmp_path / "data")
    assert r.status == "info"


# ---------------------------------------------------------------------------
# check_identity
# ---------------------------------------------------------------------------


def test_identity_ok_when_file_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile_dir = tmp_path / "data" / "profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "identity.md").write_text("# me\n", encoding="utf-8")
    _seed_db(tmp_path / "data", sessions=3, profile_versions=2)
    monkeypatch.delenv("MEMORYD_PROFILE_DIR", raising=False)
    r = doctor.check_identity(tmp_path / "data")
    assert r.status == "ok"
    assert "v2" in r.value


def test_identity_warn_when_threshold_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_db(tmp_path / "data", sessions=10)
    monkeypatch.delenv("MEMORYD_PROFILE_DIR", raising=False)
    r = doctor.check_identity(tmp_path / "data")
    assert r.status == "warn"
    assert "profile rewrite" in (r.hint or "")


def test_identity_info_when_below_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_db(tmp_path / "data", sessions=2)
    monkeypatch.delenv("MEMORYD_PROFILE_DIR", raising=False)
    r = doctor.check_identity(tmp_path / "data")
    assert r.status == "info"


# ---------------------------------------------------------------------------
# CC hooks
# ---------------------------------------------------------------------------


def test_cc_session_start_hook_ok_when_present(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    script = tmp_path / "session-start.py"
    script.write_text("# noop\n")
    _write_cc_settings(settings, session_start=f'python3 "{script}"')
    r = doctor.check_cc_session_start_hook(settings_path=settings)
    assert r.status == "ok"


def test_cc_session_start_hook_warn_when_script_missing(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    _write_cc_settings(settings, session_start='python3 "/missing/path/session-start.py"')
    r = doctor.check_cc_session_start_hook(settings_path=settings)
    assert r.status == "warn"
    assert "missing" in r.value


def test_cc_session_start_hook_fail_when_not_registered(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    _write_cc_settings(settings, session_end="/some/cmd")
    r = doctor.check_cc_session_start_hook(settings_path=settings)
    assert r.status == "fail"
    assert "install-cc-hook" in (r.hint or "")


def test_cc_session_start_hook_fail_when_no_settings(tmp_path: Path) -> None:
    r = doctor.check_cc_session_start_hook(settings_path=tmp_path / "nope.json")
    assert r.status == "fail"


def test_cc_session_end_hook_ok(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    script = tmp_path / "session-end.py"
    script.write_text("# noop\n")
    _write_cc_settings(settings, session_end=f'python3 "{script}"')
    r = doctor.check_cc_session_end_hook(settings_path=settings)
    assert r.status == "ok"


# ---------------------------------------------------------------------------
# Codex notify wrapper
# ---------------------------------------------------------------------------


def test_codex_notify_info_when_not_installed(tmp_path: Path) -> None:
    r = doctor.check_codex_notify(codex_config=tmp_path / "nope.toml")
    assert r.status == "info"


def test_codex_notify_info_when_not_wrapped(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('notify = ["/Applications/Codex.app", "turn-ended"]\n')
    r = doctor.check_codex_notify(codex_config=cfg)
    assert r.status == "info"
    assert "swap-codex-notify" in (r.hint or "")


def test_codex_notify_ok_when_wrapper_registered(tmp_path: Path) -> None:
    wrapper = tmp_path / "notify-wrapper.sh"
    wrapper.write_text("#!/bin/sh\n")
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'notify = ["{wrapper}", "turn-ended"]\n')
    r = doctor.check_codex_notify(codex_config=cfg)
    assert r.status == "ok"


def test_codex_notify_warn_when_wrapper_missing(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('notify = ["/missing/notify-wrapper.sh", "turn-ended"]\n')
    r = doctor.check_codex_notify(codex_config=cfg)
    assert r.status == "warn"


# ---------------------------------------------------------------------------
# launchd
# ---------------------------------------------------------------------------


def test_launchd_mirror_ok_when_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    fake_out = "552\t0\tcom.memoryd.mirror\n"
    r = doctor.check_launchd_mirror(launchctl_output=fake_out, plist_dir=tmp_path)
    assert r.status == "ok"
    assert "PID 552" in r.value


def test_launchd_mirror_warn_when_plist_present_but_not_loaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    plist = tmp_path / "com.memoryd.mirror.plist"
    plist.write_text("<plist/>\n")
    r = doctor.check_launchd_mirror(launchctl_output="", plist_dir=tmp_path)
    assert r.status == "warn"
    assert "bootstrap" in (r.hint or "")


def test_launchd_mirror_warn_when_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    r = doctor.check_launchd_mirror(launchctl_output="", plist_dir=tmp_path)
    assert r.status == "warn"
    assert "install-launchd-mirror" in (r.hint or "")


def test_launchd_mirror_info_on_non_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    r = doctor.check_launchd_mirror(launchctl_output=None)
    assert r.status == "info"


def test_launchd_cron_decay_warn_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    r = doctor.check_launchd_cron("decay", launchctl_output="", plist_dir=tmp_path)
    assert r.status == "warn"
    assert "install-cron --decay" in (r.hint or "")


def test_launchd_cron_weekly_identity_warn_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    r = doctor.check_launchd_cron("weekly_identity", launchctl_output="", plist_dir=tmp_path)
    assert "--weekly-identity" in (r.hint or "")


# ---------------------------------------------------------------------------
# LLM provider
# ---------------------------------------------------------------------------


def _stub_config(monkeypatch: pytest.MonkeyPatch, *, provider: str, model: str = "x") -> None:
    """Replace ``memoryd.config.load_config`` with one returning a fake cfg."""
    from memoryd import config as cfg_mod

    class _Stub(dict):
        pass

    stub = _Stub({
        "llm": {
            "provider": provider,
            "model": model,
            "api_key_env": "ANTHROPIC_API_KEY",
        }
    })
    monkeypatch.setattr(cfg_mod, "load_config", lambda: stub)


def test_llm_provider_warn_when_anthropic_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_config(monkeypatch, provider="anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = doctor.check_llm_provider()
    assert r.status == "warn"
    assert "claude-code" in (r.hint or "")


def test_llm_provider_ok_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_config(monkeypatch, provider="anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    r = doctor.check_llm_provider()
    assert r.status == "ok"


def test_llm_provider_claude_code_ok_when_cli_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_config(monkeypatch, provider="claude-code")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/local/bin/claude" if name == "claude" else None)
    r = doctor.check_llm_provider()
    assert r.status == "ok"
    assert "claude-code" in r.value


def test_llm_provider_claude_code_warn_when_cli_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_config(monkeypatch, provider="claude-code")
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    r = doctor.check_llm_provider()
    assert r.status == "warn"


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def test_mcp_warn_when_no_claude_json(tmp_path: Path) -> None:
    r = doctor.check_mcp_registered(claude_json=tmp_path / "nope.json")
    assert r.status == "warn"


def test_mcp_warn_when_memoryd_not_registered(tmp_path: Path) -> None:
    cj = tmp_path / ".claude.json"
    cj.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    r = doctor.check_mcp_registered(claude_json=cj)
    assert r.status == "warn"
    assert "memoryd" in (r.hint or "")


def test_mcp_warn_when_legacy_memoryd_server(tmp_path: Path) -> None:
    """Legacy ``memoryd-server`` is the headline upgrade hint."""
    cj = tmp_path / ".claude.json"
    cj.write_text(json.dumps({
        "mcpServers": {
            "memoryd": {"command": "/path/to/memoryd-server", "args": []}
        }
    }))
    r = doctor.check_mcp_registered(claude_json=cj)
    assert r.status == "warn"
    assert "legacy" in r.value
    assert "memoryd-mcp" in (r.hint or "")


def test_mcp_ok_when_new_memoryd_mcp(tmp_path: Path) -> None:
    cj = tmp_path / ".claude.json"
    cj.write_text(json.dumps({
        "mcpServers": {
            "memoryd": {"command": "/path/to/memoryd-mcp", "args": []}
        }
    }))
    r = doctor.check_mcp_registered(claude_json=cj)
    assert r.status == "ok"


# ---------------------------------------------------------------------------
# recent capture
# ---------------------------------------------------------------------------


def test_recent_capture_ok_with_fresh_session(tmp_path: Path) -> None:
    _seed_db(tmp_path / "data", sessions=3, sessions_recent=3)
    r = doctor.check_recent_capture(tmp_path / "data", days=7)
    assert r.status == "ok"


def test_recent_capture_warn_when_all_old(tmp_path: Path) -> None:
    _seed_db(tmp_path / "data", sessions=3, sessions_recent=0)
    r = doctor.check_recent_capture(tmp_path / "data", days=7)
    assert r.status == "warn"


def test_recent_capture_info_when_no_sessions(tmp_path: Path) -> None:
    _seed_db(tmp_path / "data")
    r = doctor.check_recent_capture(tmp_path / "data", days=7)
    assert r.status == "info"


# ---------------------------------------------------------------------------
# overall + formatters
# ---------------------------------------------------------------------------


def test_overall_status_picks_worst() -> None:
    results = [
        doctor.CheckResult("a", "a", "ok", "x"),
        doctor.CheckResult("b", "b", "warn", "x"),
        doctor.CheckResult("c", "c", "info", "x"),
    ]
    assert doctor.overall_status(results) == "warn"
    results.append(doctor.CheckResult("d", "d", "fail", "x"))
    assert doctor.overall_status(results) == "fail"


def test_overall_status_all_ok() -> None:
    results = [
        doctor.CheckResult("a", "a", "ok", "x"),
        doctor.CheckResult("b", "b", "info", "x"),
    ]
    assert doctor.overall_status(results) == "ok"


def test_format_human_skips_ok_in_quiet() -> None:
    results = [
        doctor.CheckResult("a", "AAA", "ok", "fine"),
        doctor.CheckResult("b", "BBB", "warn", "uh oh", hint="do X"),
    ]
    text = doctor.format_human(results, quiet=True)
    assert "AAA" not in text
    assert "BBB" in text
    assert "do X" in text


def test_format_human_default_includes_ok() -> None:
    results = [
        doctor.CheckResult("a", "AAA", "ok", "fine"),
        doctor.CheckResult("b", "BBB", "warn", "uh oh"),
    ]
    text = doctor.format_human(results, quiet=False)
    assert "AAA" in text
    assert "BBB" in text


def test_format_json_schema() -> None:
    results = [
        doctor.CheckResult("a", "AAA", "ok", "fine"),
        doctor.CheckResult("b", "BBB", "warn", "uh oh", hint="do X"),
    ]
    payload = json.loads(doctor.format_json(results))
    assert payload["overall"] == "warn"
    assert len(payload["checks"]) == 2
    ids = {c["id"] for c in payload["checks"]}
    assert ids == {"a", "b"}
    assert payload["checks"][1]["hint"] == "do X"
    assert "ok=1" in payload["summary"]


# ---------------------------------------------------------------------------
# run_all_checks orchestration
# ---------------------------------------------------------------------------


def test_run_all_checks_returns_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end run with a controlled data root."""
    # Stub out the bits that read real-user state.
    monkeypatch.setattr(doctor, "_launchctl_list", lambda: "")
    monkeypatch.setattr(doctor, "_read_claude_settings", lambda path=None: {})
    monkeypatch.setattr(doctor, "check_codex_notify",
                        lambda **_kw: doctor.CheckResult("codex_notify", "Codex", "info", "n/a"))
    monkeypatch.setattr(doctor, "check_mcp_registered",
                        lambda **_kw: doctor.CheckResult("mcp_registered", "MCP", "warn", "n/a"))
    monkeypatch.setattr(doctor, "check_llm_provider",
                        lambda: doctor.CheckResult("llm_provider", "LLM", "ok", "stub"))
    _seed_db(tmp_path / "data", sessions=2, sessions_recent=2)
    results = doctor.run_all_checks(data_root=tmp_path / "data")
    ids = [r.id for r in results]
    assert "binary" in ids
    assert "data_root" in ids
    assert "memory_counts" in ids
    assert "entities" in ids
    assert "recent_capture" in ids


# ---------------------------------------------------------------------------
# launchd plist health (real incident 2026-06-05: poisoned plists said "ok")
# ---------------------------------------------------------------------------


def _write_plist(tmp_path, label: str, argv: list[str]):
    import plistlib

    p = tmp_path / f"{label}.plist"
    with open(p, "wb") as f:
        plistlib.dump({"Label": label, "ProgramArguments": argv}, f)
    return p


def test_launchd_cron_fails_when_program_missing(tmp_path):
    """plist points at a binary that no longer exists (/tmp/fresh-install/...
    after a smoke test) — was reported 'ok (loaded)' for two weeks."""
    label = doctor._LAUNCHD_LABELS["decay"]
    _write_plist(tmp_path, label, ["/tmp/nonexistent-bin/memoryd", "decay-sweep"])
    r = doctor.check_launchd_cron(
        "decay", launchctl_output=f"-\t0\t{label}", plist_dir=tmp_path
    )
    assert r.status == "fail"
    assert "install-cron" in (r.hint or "")


def test_launchd_cron_fails_when_interpreter_has_no_script(tmp_path):
    """ProgramArguments == [python3, 'decay-sweep'] — interpreter exists but
    the script arg isn't a file; launchd exits 2 forever (renderer regression)."""
    label = doctor._LAUNCHD_LABELS["decay"]
    fake_py = tmp_path / "python3"
    fake_py.touch()
    _write_plist(tmp_path, label, [str(fake_py), "decay-sweep"])
    r = doctor.check_launchd_cron(
        "decay", launchctl_output=f"-\t0\t{label}", plist_dir=tmp_path
    )
    assert r.status == "fail"


def test_launchd_cron_warns_on_nonzero_last_exit(tmp_path):
    label = doctor._LAUNCHD_LABELS["decay"]
    fake_bin = tmp_path / "memoryd"
    fake_bin.touch()
    _write_plist(tmp_path, label, [str(fake_bin), "decay-sweep"])
    r = doctor.check_launchd_cron(
        "decay", launchctl_output=f"-\t78\t{label}", plist_dir=tmp_path
    )
    assert r.status == "warn"
    assert "78" in r.value


def test_launchd_cron_still_ok_when_healthy(tmp_path):
    label = doctor._LAUNCHD_LABELS["decay"]
    fake_bin = tmp_path / "memoryd"
    fake_bin.touch()
    _write_plist(tmp_path, label, [str(fake_bin), "decay-sweep"])
    r = doctor.check_launchd_cron(
        "decay", launchctl_output=f"-\t0\t{label}", plist_dir=tmp_path
    )
    assert r.status == "ok"
