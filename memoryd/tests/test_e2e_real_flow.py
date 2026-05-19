"""End-to-end smoke test for the capture → analyze → KG → inject loop.

Marked ``e2e`` so the normal ``pytest`` run skips it (it shells out to
subprocesses and writes a non-trivial fixture). Run explicitly with::

    pytest -m e2e

What's verified, in order, against a single tmp_path data root:

1. ``memoryd capture`` (subprocess, real CLI) ingests a CC-shaped
   SessionEnd payload and writes a session ``.md`` + SQLite row.
2. ``memoryd list`` finds the session row.
3. ``analyze_session`` (in-process, with a mock LLM) writes promotion
   rows AND an approve_promotion flow materializes a long-term ``.md``.
4. KG store ingestion populates ``entities`` so ``memoryd kg entities``
   has output.
5. ``memoryd search`` finds the promoted decision by text.
6. ``memoryd inject`` returns a non-empty rendered markdown block that
   mentions the user-visible identifiers we wrote.
7. ``memoryd profile rewrite --dry-run`` exits 0 (no LLM call) or
   gracefully skips when no provider is configured.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _memoryd_bin() -> str:
    bin_path = shutil.which("memoryd")
    if bin_path:
        return bin_path
    guess = Path(sys.executable).parent / "memoryd"
    if guess.exists():
        return str(guess)
    pytest.skip("memoryd binary not available in PATH; install with `uv pip install -e .`")


def _run(env_root: Path, args: list[str], *, stdin_data: str | None = None,
         extra_env: dict | None = None, timeout: int = 30,
         ) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["MEMORYD_DATA_ROOT"] = str(env_root)
    env["MEMORYD_PROFILE_DIR"] = str(env_root / "profile")
    # Make sure analyze-session subprocess (forked from capture) doesn't
    # hang waiting for an LLM in test land.
    env.pop("ANTHROPIC_API_KEY", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_memoryd_bin(), *args],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# the test
# ---------------------------------------------------------------------------


def test_full_capture_to_recall_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole loop: capture → KG → search → inject → profile dry-run."""
    data_root = tmp_path / "memoryd_data"
    data_root.mkdir()
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MEMORYD_PROFILE_DIR", str(data_root / "profile"))

    # ---- 1. craft a realistic CC SessionEnd payload + transcript -------------
    transcript = tmp_path / "transcript.jsonl"
    transcript_lines = [
        json.dumps({
            "message": {"content": "我决定 React → Solid，性能 + 体积"}
        }),
        json.dumps({
            "message": {"content": [
                {"type": "text",
                 "text": "我偏好 uv 管 venv，比 poetry 启动更快"}
            ]}
        }),
        json.dumps({
            "message": {"content": "今天还讨论了 memory-system 项目"}
        }),
    ]
    transcript.write_text("\n".join(transcript_lines), encoding="utf-8")

    cwd_proj = tmp_path / "myproj"
    cwd_proj.mkdir()
    (cwd_proj / ".git").mkdir()

    payload = {
        "session_id": "test-session-001",
        "transcript_path": str(transcript),
        "cwd": str(cwd_proj),
    }

    # ---- 2. memoryd capture (subprocess) --------------------------------------
    result = _run(
        data_root,
        ["capture", "--source=claude-code"],
        stdin_data=json.dumps(payload),
    )
    assert result.returncode == 0, (
        f"capture failed: stderr={result.stderr!r} stdout={result.stdout!r}"
    )

    # session .md was written
    md_files = list(data_root.glob("scopes/*/sessions/*.md"))
    assert len(md_files) == 1, (
        f"expected one session .md, got: {md_files}"
    )
    sess_md = md_files[0]
    body = sess_md.read_text(encoding="utf-8")
    assert "React → Solid" in body, body[:500]
    assert "uv 管 venv" in body, body[:500]

    # SQLite row was inserted by capture
    with sqlite3.connect(str(data_root / "index.db")) as conn:
        rows = conn.execute("SELECT slug, type, scope_hash FROM memories").fetchall()
    assert len(rows) == 1, rows
    session_slug = rows[0][0]
    scope_hash = rows[0][2]
    assert rows[0][1] == "session"

    # ---- 3. analyze_session with a mock LLM -----------------------------------
    # capture also forks `memoryd analyze-session` in the background; we
    # don't depend on that here — we run analyze ourselves synchronously
    # with a deterministic fake LLM so we have stable promotion rows.
    from memoryd.governance.analyze import analyze_session, approve_promotion

    class _FakeLLM:
        def complete(self, *, system: str, user: str, model: str | None = None) -> str:
            return json.dumps([
                {
                    "type": "decision",
                    "title": "React 切到 Solid",
                    "body": "决定原因：性能 + 体积更小",
                    "triggers": ["solid", "react"],
                    "supersedes": [],
                    "dura": {"D": 0.9, "U": 0.85, "R": 0.8, "A": 0.9},
                    "reasoning": "user explicitly stated a decision",
                },
                {
                    "type": "preference",
                    "title": "偏好 uv 管 venv",
                    "body": "uv 比 poetry 启动更快",
                    "triggers": ["uv", "venv"],
                    "supersedes": [],
                    "dura": {"D": 0.7, "U": 0.7, "R": 0.7, "A": 0.7},
                    "reasoning": "stated preference",
                },
            ])

    analyze_session(data_root, session_slug=session_slug, provider=_FakeLLM())

    with sqlite3.connect(str(data_root / "index.db")) as conn:
        prom_rows = conn.execute(
            "SELECT id, proposed_type, proposed_title FROM promotions"
        ).fetchall()
    assert len(prom_rows) == 2, prom_rows
    assert any("Solid" in r[2] for r in prom_rows)

    # approve one promotion → materializes the long-term .md
    promotion_id = prom_rows[0][0]
    out_path = approve_promotion(data_root, promotion_id)
    assert out_path is not None and out_path.exists(), out_path

    # ---- 4. seed KG via the store (analyze doesn't populate KG) ---------------
    from memoryd.index import open_index
    from memoryd.knowledge_graph import KnowledgeGraphStore

    idx = open_index(data_root / "index.db")
    try:
        store = KnowledgeGraphStore(idx.conn)
        store.upsert_entity("memory-system", "project", scope_hash=scope_hash)
        store.upsert_entity("memory-system", "project", scope_hash=scope_hash)
        store.upsert_entity("Solid", "library", scope_hash=scope_hash)
        store.upsert_entity("uv", "tool", scope_hash=scope_hash)
    finally:
        idx.close()

    # ---- 5. memoryd kg entities --------------------------------------------
    kg_result = _run(data_root, ["kg", "entities", "--json"])
    assert kg_result.returncode == 0, kg_result.stderr
    kg_payload = json.loads(kg_result.stdout)
    names = {e["name"] for e in kg_payload}
    assert "memory-system" in names, names
    assert "Solid" in names, names

    # ---- 6. memoryd search ---------------------------------------------------
    search_result = _run(data_root, ["search", "Solid", "--json"])
    assert search_result.returncode == 0, search_result.stderr
    search_payload = json.loads(search_result.stdout)
    assert search_payload, "expected at least one search hit"

    # ---- 7. memoryd inject ---------------------------------------------------
    inject_result = _run(data_root, ["inject", "--scope=global"])
    assert inject_result.returncode == 0, inject_result.stderr
    rendered = inject_result.stdout
    assert "## 与 abble 的最近上下文" in rendered, rendered[:300]
    assert "memory-system" in rendered, rendered[:300]
    assert "Solid" in rendered, rendered[:300]

    # ---- 8. memoryd profile rewrite --dry-run --------------------------------
    # Behavior depends on whether an LLM provider is configured:
    #   - With provider: rc=0, dry-run preview on stdout.
    #   - Without provider: rc=1 with a clear "set ANTHROPIC_API_KEY" message
    #     on stderr. This is acceptable UX — never a crash.
    # We only assert (a) no crash (no traceback), (b) one of the two paths.
    rewrite_result = _run(
        data_root,
        ["profile", "rewrite", "--dry-run", "--window-days=7"],
    )
    assert "Traceback" not in rewrite_result.stderr, (
        f"profile rewrite crashed: stderr={rewrite_result.stderr!r}"
    )
    if rewrite_result.returncode != 0:
        # Acceptable failure mode: no LLM configured.
        assert (
            "ANTHROPIC_API_KEY" in rewrite_result.stderr
            or "llm" in rewrite_result.stderr.lower()
        ), f"unexpected failure: {rewrite_result.stderr!r}"

    # ---- 9. memoryd profile show (should NOT need an LLM) --------------------
    show_result = _run(data_root, ["profile", "show"])
    # No identity.md exists yet (we never ran a real rewrite) — show should
    # still exit 0 with either empty output or a friendly placeholder.
    assert show_result.returncode == 0, (
        f"profile show failed: stderr={show_result.stderr!r}"
    )
