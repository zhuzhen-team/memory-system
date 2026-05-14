"""Markdown frontmatter schema for memory entries.

Plan 3: 6 types + governance fields (TTL / decay / DURA / promotion / relations).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

import yaml
from pydantic import BaseModel, Field


MemoryType = Literal[
    "session",
    "decision",
    "preference",
    "fact",
    "playbook",
    "warning",
]


DecayState = Literal["alive", "dim", "soft-forgotten"]


class Frontmatter(BaseModel):
    """YAML frontmatter for a memory file.

    Plan 1 base fields (title / slug / type / scope_hash / source / created_at /
    updated_at / triggers / tags / relations) plus Plan 3 governance fields.
    Every Plan 3 field is optional so Plan 1-2.5 `.md` files still parse.
    """

    title: str
    slug: str
    type: MemoryType
    scope_hash: str
    triggers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: str
    created_at: datetime
    updated_at: datetime | None = None
    relations: list[str] = Field(default_factory=list)

    # Plan 3 governance fields (all optional, sensible defaults)
    promoted_from: str | None = None
    supersedes: list[str] = Field(default_factory=list)
    ttl_days: int | None = None
    decay_state: DecayState = "alive"
    last_recalled_at: datetime | None = None
    recall_count: int = 0
    dura_score: dict[str, float] | None = None


class SessionMemory(BaseModel):
    """A single memory entry: frontmatter + free-form markdown body."""

    frontmatter: Frontmatter
    body: str

    def to_markdown(self) -> str:
        fm_dict = self.frontmatter.model_dump(mode="json", exclude_none=True)
        # exclude_none drops None fields but pydantic v2 keeps empty lists and
        # non-None scalar defaults. Strip both so Plan 1-2.5 sessions re-saved
        # by Plan 3 governance jobs don't acquire noise lines (decay_state,
        # recall_count) that match their defaults.
        for k in ("triggers", "tags", "relations", "supersedes"):
            if fm_dict.get(k) == []:
                del fm_dict[k]
        if fm_dict.get("decay_state") == "alive":
            del fm_dict["decay_state"]
        if fm_dict.get("recall_count") == 0:
            del fm_dict["recall_count"]
        fm_yaml = yaml.safe_dump(fm_dict, sort_keys=False, allow_unicode=True)
        return f"---\n{fm_yaml}---\n\n{self.body}"

    @classmethod
    def from_markdown(cls, text: str) -> "SessionMemory":
        if not text.startswith("---\n"):
            raise ValueError("Missing YAML frontmatter delimiter at start of file")
        try:
            _, fm_text, body = text.split("---\n", 2)
        except ValueError as e:
            raise ValueError("Malformed frontmatter delimiters") from e
        fm_data = yaml.safe_load(fm_text)
        if not isinstance(fm_data, dict):
            raise ValueError("Frontmatter must be a mapping")
        return cls(frontmatter=Frontmatter(**fm_data), body=body.lstrip("\n"))
