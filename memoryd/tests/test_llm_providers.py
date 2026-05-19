"""Tests for the three async providers (anthropic / openai / ollama).

Every test mocks the underlying SDK / HTTP — no network is touched.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from memoryd.llm import (
    AnthropicAsyncProvider,
    LLMMessage,
    OllamaAsyncProvider,
    OpenAIAsyncProvider,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---------------------------------------------------------------------------
# AnthropicAsyncProvider
# ---------------------------------------------------------------------------


def _make_anthropic_client(text: str | list[str]) -> MagicMock:
    if isinstance(text, str):
        blocks = [SimpleNamespace(type="text", text=text)]
    else:
        blocks = [SimpleNamespace(type="text", text=t) for t in text]
    fake_msg = SimpleNamespace(content=blocks)
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=fake_msg)
    return client


def test_anthropic_generate_splits_system_message():
    client = _make_anthropic_client("hello")
    p = AnthropicAsyncProvider(client=client, model="claude-haiku-4-5")
    out = _run(
        p.generate(
            [
                LLMMessage(role="system", content="你是助手"),
                LLMMessage(role="user", content="hi"),
            ]
        )
    )
    assert out == "hello"
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["system"] == "你是助手"
    # System message removed from `messages`
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_generate_concatenates_multi_block_text():
    client = _make_anthropic_client(["foo", "bar"])
    p = AnthropicAsyncProvider(client=client)
    out = _run(p.generate([LLMMessage(role="user", content="hi")]))
    assert out == "foobar"


def test_anthropic_generate_json_appends_json_hint_to_system():
    client = _make_anthropic_client('{"x": 1}')
    p = AnthropicAsyncProvider(client=client)
    _run(
        p.generate(
            [
                LLMMessage(role="system", content="原始 system"),
                LLMMessage(role="user", content="u"),
            ],
            json_mode=True,
        )
    )
    kwargs = client.messages.create.call_args.kwargs
    assert "严格" in kwargs["system"] and "JSON" in kwargs["system"]
    assert "原始 system" in kwargs["system"]


def test_anthropic_generate_json_parses_pydantic_schema():
    class _Out(BaseModel):
        x: int
        name: str

    client = _make_anthropic_client('{"x": 7, "name": "tester"}')
    p = AnthropicAsyncProvider(client=client)
    out = _run(p.generate_json([LLMMessage(role="user", content="u")], _Out))
    assert isinstance(out, _Out)
    assert out.x == 7 and out.name == "tester"


def test_anthropic_generate_json_strips_markdown_fences():
    client = _make_anthropic_client('```json\n{"y": 2}\n```')
    p = AnthropicAsyncProvider(client=client)
    out = _run(p.generate_json([LLMMessage(role="user", content="u")], {}))
    assert out == {"y": 2}


def test_anthropic_generate_json_recovers_from_trailing_prose():
    """Model adds prose after the JSON; we should still parse."""
    client = _make_anthropic_client('{"a": 1} \n  some explanation here')
    p = AnthropicAsyncProvider(client=client)
    out = _run(p.generate_json([LLMMessage(role="user", content="u")], {}))
    assert out == {"a": 1}


# ---------------------------------------------------------------------------
# OpenAIAsyncProvider
# ---------------------------------------------------------------------------


def _make_openai_client(text: str) -> MagicMock:
    choice = SimpleNamespace(message=SimpleNamespace(content=text))
    response = SimpleNamespace(choices=[choice])
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client


def test_openai_generate_forwards_messages_verbatim():
    client = _make_openai_client("response text")
    p = OpenAIAsyncProvider(client=client, model="gpt-4o-mini")
    out = _run(
        p.generate(
            [
                LLMMessage(role="system", content="sys"),
                LLMMessage(role="user", content="u"),
            ]
        )
    )
    assert out == "response text"
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u"},
    ]
    assert "response_format" not in kwargs


def test_openai_generate_sets_json_response_format_when_json_mode():
    client = _make_openai_client('{"k": "v"}')
    p = OpenAIAsyncProvider(client=client)
    _run(
        p.generate(
            [LLMMessage(role="user", content="u")],
            json_mode=True,
        )
    )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}


def test_openai_generate_json_returns_pydantic_instance():
    class _M(BaseModel):
        score: float

    client = _make_openai_client('{"score": 0.92}')
    p = OpenAIAsyncProvider(client=client)
    out = _run(p.generate_json([LLMMessage(role="user", content="u")], _M))
    assert isinstance(out, _M)
    assert out.score == pytest.approx(0.92)


def test_openai_generate_handles_empty_choices_list():
    client = MagicMock()
    response = SimpleNamespace(choices=[])
    client.chat.completions.create = AsyncMock(return_value=response)
    p = OpenAIAsyncProvider(client=client)
    out = _run(p.generate([LLMMessage(role="user", content="u")]))
    assert out == ""


# ---------------------------------------------------------------------------
# OllamaAsyncProvider
# ---------------------------------------------------------------------------


def test_ollama_generate_calls_chat_endpoint():
    captured = {}

    def fake_post(path: str, body: dict) -> dict:
        captured["path"] = path
        captured["body"] = body
        return {"message": {"role": "assistant", "content": "你好"}}

    p = OllamaAsyncProvider(model="qwen2.5:7b", post=fake_post)
    out = _run(
        p.generate(
            [
                LLMMessage(role="system", content="sys"),
                LLMMessage(role="user", content="hi"),
            ],
            max_tokens=512,
            temperature=0.1,
        )
    )
    assert out == "你好"
    assert captured["path"] == "/api/chat"
    assert captured["body"]["model"] == "qwen2.5:7b"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert captured["body"]["stream"] is False
    assert captured["body"]["options"]["num_predict"] == 512
    assert captured["body"]["options"]["temperature"] == 0.1
    # No json mode → no format key
    assert "format" not in captured["body"]


def test_ollama_generate_sets_format_json_when_json_mode():
    captured = {}

    def fake_post(path: str, body: dict) -> dict:
        captured["body"] = body
        return {"message": {"content": '{"ok": true}'}}

    p = OllamaAsyncProvider(post=fake_post)
    _run(
        p.generate(
            [LLMMessage(role="user", content="hi")],
            json_mode=True,
        )
    )
    assert captured["body"]["format"] == "json"


def test_ollama_generate_json_parses_pydantic_schema():
    class _O(BaseModel):
        ok: bool

    def fake_post(path, body):
        return {"message": {"content": '{"ok": true}'}}

    p = OllamaAsyncProvider(post=fake_post)
    out = _run(p.generate_json([LLMMessage(role="user", content="u")], _O))
    assert isinstance(out, _O)
    assert out.ok is True


def test_ollama_base_url_can_be_overridden(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    p = OllamaAsyncProvider(base_url="http://192.168.1.10:11434/")
    assert p.base_url == "http://192.168.1.10:11434"


def test_ollama_base_url_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host:9999")
    p = OllamaAsyncProvider()
    assert p.base_url == "http://host:9999"


def test_ollama_returns_empty_string_when_no_message_content():
    def fake_post(path, body):
        return {}  # malformed response

    p = OllamaAsyncProvider(post=fake_post)
    out = _run(p.generate([LLMMessage(role="user", content="hi")]))
    assert out == ""
