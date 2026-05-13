"""Markdown frontmatter schema for memory entries.

v1.0-α: only supports `session` type. Other types (decision/preference/fact)
land in plan 3.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

import yaml
from pydantic import BaseModel, Field


MemoryType = Literal["session"]
"""v1.0-α scope. Plan 3 expands to: decision | preference | fact | playbook | warning."""


class Frontmatter(BaseModel):
    """YAML frontmatter for a memory file.

    Fields chosen to be compatible with Basic Memory schema where reasonable:
    - `title`, `slug`, `type`, `created_at`, `updated_at` are common
    - `triggers` is our addition for keyword routing (per OpenClaw Tier 3)
    - `scope_hash` ties the memory to its directory scope
    """

    title: str
    slug: str
    type: MemoryType
    scope_hash: str
    triggers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: str  # e.g. "claude-code", "codex", "openclaw", "manual"
    created_at: datetime
    updated_at: datetime | None = None
    relations: list[str] = Field(default_factory=list)


class SessionMemory(BaseModel):
    """A single memory entry: frontmatter + free-form markdown body."""

    frontmatter: Frontmatter
    body: str

    def to_markdown(self) -> str:
        """Serialize to a string with YAML frontmatter + body."""
        fm_dict = self.frontmatter.model_dump(mode="json", exclude_none=True)
        fm_yaml = yaml.safe_dump(fm_dict, sort_keys=False, allow_unicode=True)
        return f"---\n{fm_yaml}---\n\n{self.body}"

    @classmethod
    def from_markdown(cls, text: str) -> "SessionMemory":
        """Parse a markdown string with YAML frontmatter."""
        if not text.startswith("---\n"):
            raise ValueError("Missing YAML frontmatter delimiter at start of file")
        try:
            _, fm_text, body = text.split("---\n", 2)
        except ValueError as e:
            raise ValueError("Malformed frontmatter delimiters") from e
        fm_data = yaml.safe_load(fm_text)
        if not isinstance(fm_data, dict):
            raise ValueError(
                f"YAML frontmatter must be a mapping, got {type(fm_data).__name__}"
            )
        return cls(
            frontmatter=Frontmatter(**fm_data),
            body=body.lstrip("\n"),
        )
