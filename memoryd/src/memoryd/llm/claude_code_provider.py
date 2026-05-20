"""Claude Code CLI provider — reuse local ``claude`` binary as a free LLM.

If the user has Claude Code installed (and is logged in), the ``claude -p``
print mode acts as a headless one-shot LLM endpoint. No ``ANTHROPIC_API_KEY``
needed — the request goes through CC's session token, billed against the
user's CC subscription quota instead of pay-per-token.

This is the cheapest path for weekly profile rewrites / monthly reports for
anyone already on a CC subscription.

Limitations:
- The ``claude`` binary cold-start adds ~1-3s overhead per call (fine for
  weekly cron jobs, painful for tight loops — don't use this for live MCP
  tool calls).
- JSON mode is *not* a hard guarantee — we lean on the prompt + the same
  fence-stripping fallback the Anthropic provider uses.
- No streaming.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from typing import Any

from pydantic import BaseModel

from .base import LLMMessage, LLMUnavailable, split_system

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_TIMEOUT = 120.0


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _run_sync(coro: Any) -> str:
    """Run an awaitable from a sync context, even if a loop is already running.

    Profile.rewrite / governance.analyze etc. call sync ``provider.complete()``
    from inside an async pipeline; plain ``asyncio.run()`` raises in that case.
    Detect a running loop and dispatch the coroutine to a fresh worker thread
    so we get one independent ``asyncio.run`` per call.
    """
    import concurrent.futures
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(asyncio.run, coro)
        return fut.result()


def _strip_json_envelope(raw: str) -> str:
    """Pull JSON out of markdown fences / leading prose CC tends to add."""
    raw = raw.strip()
    m = _FENCE_RE.search(raw)
    if m:
        return m.group(1).strip()
    # Find the first { or [ and take from there.
    for i, ch in enumerate(raw):
        if ch in "{[":
            return raw[i:].strip()
    return raw


class ClaudeCodeProvider:
    """Spawn ``claude -p`` for each generate call.

    The full prompt (system + messages concatenated) is fed via stdin, the
    assistant's reply is read from stdout. Stderr is captured for diagnostics
    on non-zero exit.

    Constructor accepts a ``binary`` override and a ``spawn`` injector so unit
    tests can replace the subprocess without monkeypatching ``asyncio``.
    """

    name = "claude-code"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        binary: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        spawn: Any = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        if binary is not None:
            self._binary = binary
        else:
            self._binary = (
                os.environ.get("MEMORYD_CLAUDE_BIN")
                or shutil.which("claude")
                or "claude"
            )
        self._spawn = spawn  # tests inject; None = real subprocess

    @staticmethod
    def _flatten(messages: list[LLMMessage]) -> str:
        """Merge a chat-style list into a single prompt CC can consume.

        We keep an explicit ``System:`` block so model instructions survive
        the round-trip, then label user vs assistant turns. CC's ``-p`` mode
        treats stdin as one prompt so we just concatenate with headers.
        """
        system_text, rest = split_system(messages)
        parts: list[str] = []
        if system_text:
            parts.append("System:\n" + system_text)
        for m in rest:
            role = m["role"].capitalize()
            parts.append(f"{role}:\n{m['content']}")
        # Trailing nudge — without this CC sometimes echoes the last user
        # message instead of replying.
        parts.append("Assistant:")
        return "\n\n".join(parts)

    async def _run(self, prompt: str) -> str:
        if self._spawn is not None:
            # Test path — caller provides (stdout, returncode) directly.
            return await self._spawn(self._binary, self.model, prompt, self.timeout)
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                "-p",
                "--model", self.model,
                "--output-format", "json",  # 结构化 JSON 输出，比 raw text 解析更稳
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LLMUnavailable(
                f"claude CLI not found at {self._binary!r}. "
                "Install Claude Code or set MEMORYD_CLAUDE_BIN."
            ) from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise LLMUnavailable(
                f"claude CLI timed out after {self.timeout}s"
            ) from exc

        if proc.returncode != 0:
            raise LLMUnavailable(
                f"claude CLI exited {proc.returncode}: "
                f"{stderr_b.decode('utf-8', 'replace')[:300]}"
            )
        raw = stdout_b.decode("utf-8", "replace").strip()
        # With --output-format=json, claude wraps the reply in
        #   {"type":"result","subtype":"success","result":"...assistant text...","is_error":false,...}
        # Unwrap to the actual assistant text so downstream parsers don't see meta.
        try:
            envelope = json.loads(raw)
            if isinstance(envelope, dict) and envelope.get("type") == "result":
                if envelope.get("is_error"):
                    raise LLMUnavailable(
                        f"claude CLI returned error: {envelope.get('result','')[:200]}"
                    )
                return str(envelope.get("result", "")).strip()
        except (json.JSONDecodeError, ValueError):
            pass  # fall through: maybe CLI version didn't honor --output-format
        return raw

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 2048,  # noqa: ARG002 - claude CLI controls its own limits
        temperature: float = 0.2,  # noqa: ARG002
        json_mode: bool = False,
    ) -> str:
        prompt = self._flatten(messages)
        if json_mode:
            prompt += (
                "\n\nIMPORTANT: respond with valid JSON only, no markdown fences, "
                "no commentary."
            )
        return await self._run(prompt)

    async def generate_json(
        self,
        messages: list[LLMMessage],
        schema: type[BaseModel] | dict,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> BaseModel | dict:
        raw = await self.generate(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=True,
        )
        body = _strip_json_envelope(raw)
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise LLMUnavailable(
                f"claude CLI returned non-JSON output: {body[:200]!r}"
            ) from exc
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return schema.model_validate(data)
        return data

    # ----------------------------------------------------------------
    # Legacy sync interface (Plan 3 LegacyLLMProvider protocol).
    # Used by governance/analyze.py and other pre-async call sites via
    # memoryd.llm.get_provider(). Spawns a fresh `claude -p` per call,
    # same as async generate() but driven by asyncio.run() under the hood.
    # ----------------------------------------------------------------

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
    ) -> str:
        """Synchronous one-shot completion — Plan 3 LegacyLLMProvider shape.

        Lets ``memoryd.llm.get_provider()`` return a ClaudeCodeProvider, so
        legacy sync callers (analyze-session DURA + entity extraction) work
        without any rewrites.
        """
        msgs: list[LLMMessage] = []
        if system:
            msgs.append(LLMMessage(role="system", content=system))
        msgs.append(LLMMessage(role="user", content=user))
        if model is not None and model != self.model:
            # Honour the per-call override without mutating instance state.
            override = ClaudeCodeProvider(
                model=model,
                binary=self._binary,
                timeout=self.timeout,
                spawn=self._spawn,
            )
            coro = override.generate(msgs)
        else:
            coro = self.generate(msgs)
        return _run_sync(coro)
