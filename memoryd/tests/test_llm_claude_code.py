"""Tests for the claude-code LLM provider (spawns `claude -p` CLI)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from memoryd.llm.base import LLMMessage, LLMUnavailable
from memoryd.llm.claude_code_provider import (
    ClaudeCodeProvider,
    _strip_json_envelope,
)
from memoryd.llm.factory import get_llm


def _make(provider_spawn: Any) -> ClaudeCodeProvider:
    return ClaudeCodeProvider(model="claude-haiku-4-5", spawn=provider_spawn)


def test_flatten_includes_system_user_assistant_trailer() -> None:
    p = ClaudeCodeProvider()
    prompt = p._flatten([
        LLMMessage(role="system", content="be terse"),
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="assistant", content="hello"),
        LLMMessage(role="user", content="bye"),
    ])
    assert "System:\nbe terse" in prompt
    assert "User:\nhi" in prompt
    assert "Assistant:\nhello" in prompt
    assert "User:\nbye" in prompt
    # Trailing nudge prevents CC from echoing the last user turn.
    assert prompt.rstrip().endswith("Assistant:")


def test_generate_invokes_spawn_with_model_and_returns_stdout() -> None:
    captured: dict[str, Any] = {}

    async def fake_spawn(binary: str, model: str, prompt: str, timeout: float) -> str:
        captured["binary"] = binary
        captured["model"] = model
        captured["prompt"] = prompt
        captured["timeout"] = timeout
        return "OK"

    p = _make(fake_spawn)
    out = asyncio.run(p.generate([LLMMessage(role="user", content="say only OK")]))
    assert out == "OK"
    assert captured["model"] == "claude-haiku-4-5"
    assert "say only OK" in captured["prompt"]


def test_generate_json_mode_appends_strict_instruction() -> None:
    captured: dict[str, Any] = {}

    async def fake_spawn(binary, model, prompt, timeout):
        captured["prompt"] = prompt
        return '{"ok": true}'

    p = _make(fake_spawn)
    out = asyncio.run(
        p.generate([LLMMessage(role="user", content="ping")], json_mode=True)
    )
    assert out == '{"ok": true}'
    assert "valid JSON only" in captured["prompt"]


def test_generate_json_strips_markdown_fences() -> None:
    async def fake_spawn(binary, model, prompt, timeout):
        return "Sure! Here you go:\n```json\n{\"name\": \"abble\", \"n\": 3}\n```\n"

    class Out(BaseModel):
        name: str
        n: int

    p = _make(fake_spawn)
    result = asyncio.run(
        p.generate_json([LLMMessage(role="user", content="x")], Out)
    )
    assert isinstance(result, Out)
    assert result.name == "abble"
    assert result.n == 3


def test_generate_json_raises_on_invalid_json() -> None:
    async def fake_spawn(binary, model, prompt, timeout):
        return "definitely not json"

    p = _make(fake_spawn)
    with pytest.raises(LLMUnavailable):
        asyncio.run(p.generate_json([LLMMessage(role="user", content="x")], {}))


def test_factory_returns_claude_code_provider() -> None:
    p = get_llm(provider="claude-code", model="claude-sonnet-4-6", binary="/bin/true")
    assert isinstance(p, ClaudeCodeProvider)
    assert p.name == "claude-code"
    assert p.model == "claude-sonnet-4-6"


def test_strip_json_envelope_finds_json_in_prose() -> None:
    assert _strip_json_envelope('blah blah {"a": 1}  ') == '{"a": 1}'
    assert _strip_json_envelope("```json\n[1,2,3]\n```") == "[1,2,3]"
    assert _strip_json_envelope('   [{"a":1}]   ') == '[{"a":1}]'
    assert _strip_json_envelope("plain text") == "plain text"


def test_missing_binary_raises_llm_unavailable() -> None:
    """Real spawn path: claude binary not found → LLMUnavailable, not crash."""
    p = ClaudeCodeProvider(model="x", binary="/no/such/claude-bin")
    with pytest.raises(LLMUnavailable, match="claude CLI not found"):
        asyncio.run(p.generate([LLMMessage(role="user", content="hi")]))


def test_timeout_kills_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider raises LLMUnavailable rather than hanging on a stuck CLI."""
    class FakeProc:
        returncode = None

        async def communicate(self, _input):
            await asyncio.sleep(10)  # would block forever
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

    async def fake_create(*_args, **_kwargs):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    p = ClaudeCodeProvider(model="x", binary="/bin/true", timeout=0.05)
    with pytest.raises(LLMUnavailable, match="timed out"):
        asyncio.run(p.generate([LLMMessage(role="user", content="x")]))


# ---------------------------------------------------------------------------
# Binary discovery (_discover_claude_bin)
# ---------------------------------------------------------------------------


def test_discover_claude_bin_env_override_wins(monkeypatch):
    from memoryd.llm import claude_code_provider as ccp

    monkeypatch.setenv("MEMORYD_CLAUDE_BIN", "/custom/claude")
    assert ccp._discover_claude_bin() == "/custom/claude"


def test_discover_claude_bin_falls_back_to_known_locations(monkeypatch, tmp_path):
    """launchd / GUI processes run with a minimal PATH (no ~/.local/bin):
    which() fails there and the literal 'claude' fallback can never spawn.
    Real incident: monthly-report exit 1 in launchd, MCP judge always
    degraded with 'claude CLI not found'."""
    from memoryd.llm import claude_code_provider as ccp

    monkeypatch.delenv("MEMORYD_CLAUDE_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    claude = local_bin / "claude"
    claude.touch()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert ccp._discover_claude_bin() == str(claude)


def test_discover_claude_bin_literal_last_resort(monkeypatch, tmp_path):
    from memoryd.llm import claude_code_provider as ccp

    monkeypatch.delenv("MEMORYD_CLAUDE_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)  # empty home
    monkeypatch.setattr(ccp, "_EXTRA_FALLBACK_DIRS", ())
    assert ccp._discover_claude_bin() == "claude"
