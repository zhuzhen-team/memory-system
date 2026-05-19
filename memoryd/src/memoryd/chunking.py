"""Markdown chunking — split markdown files into semantic chunks by headings.

Adapted from memsearch chunker.py (MIT, Zilliz).

CJK-friendly sentence boundaries, SHA256 content hash for dedup, and a
helper to strip metadata noise before embedding. Chunks are produced as
frozen dataclasses; `compute_chunk_id` mirrors the composite ID format
used by OpenClaw / memsearch.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Minimum meaningful text length after stripping metadata noise.
# Chunks with less useful text than this are dropped during chunking.
_MIN_MEANINGFUL_LEN = 2


def clean_content_for_embedding(text: str) -> str:
    """Strip metadata noise from chunk content before embedding.

    Removes HTML comments (``<!-- ... -->``), which often contain session/turn
    UUIDs and transcript paths that dilute embedding quality.  The original
    content stored in the vector DB is unchanged — this only affects the text
    sent to the embedding model.
    """
    cleaned = _HTML_COMMENT_RE.sub("", text)
    # Collapse runs of blank lines left behind by removed comments
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _has_meaningful_content(text: str) -> bool:
    """Return True if *text* has enough substance to be worth indexing.

    Strips HTML comments, heading lines, and whitespace, then checks
    whether the remaining body text meets the minimum length threshold.
    A section like ``## Session 03:16`` with no body is rejected, while
    ``## Title\\nSome real content`` is kept.
    """
    stripped = _HTML_COMMENT_RE.sub("", text)
    lines = [ln for ln in stripped.splitlines() if not _HEADING_RE.match(ln)]
    body = "\n".join(lines).strip()
    return len(body) >= _MIN_MEANINGFUL_LEN


@dataclass(frozen=True)
class Chunk:
    """A single chunk extracted from a markdown document."""

    content: str
    source: str  # file path or memory slug
    heading: str  # nearest heading (empty string for preamble)
    heading_level: int  # 0 for preamble
    start_line: int
    end_line: int
    content_hash: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.content_hash:
            h = hashlib.sha256(self.content.encode()).hexdigest()[:16]
            object.__setattr__(self, "content_hash", h)


def compute_chunk_id(
    source: str,
    start_line: int,
    end_line: int,
    content_hash: str,
    model: str,
) -> str:
    """Compute a composite chunk ID.

    Format: ``sha256(markdown:source:startLine:endLine:contentHash:model)[:16]``.
    The model component lets the same source/line range coexist under multiple
    embedding models without collision.
    """
    raw = f"markdown:{source}:{start_line}:{end_line}:{content_hash}:{model}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def chunk_markdown(
    text: str,
    source: str = "",
    *,
    max_chunk_size: int = 1500,
    overlap_lines: int = 2,
) -> list[Chunk]:
    """Split markdown *text* into chunks, breaking on headings.

    Chunks that exceed *max_chunk_size* characters are split further at
    paragraph boundaries.  A small *overlap_lines* context is carried
    forward to preserve continuity.
    """
    lines = text.split("\n")
    heading_positions: list[tuple[int, int, str]] = []  # (line_idx, level, title)
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            heading_positions.append((i, len(m.group(1)), m.group(2).strip()))

    sections: list[tuple[int, int, str, int]] = []  # (start, end, heading, level)
    if not heading_positions or heading_positions[0][0] > 0:
        end = heading_positions[0][0] if heading_positions else len(lines)
        sections.append((0, end, "", 0))

    for idx, (line_idx, level, title) in enumerate(heading_positions):
        next_start = heading_positions[idx + 1][0] if idx + 1 < len(heading_positions) else len(lines)
        sections.append((line_idx, next_start, title, level))

    chunks: list[Chunk] = []
    for start, end, heading, level in sections:
        section_text = "\n".join(lines[start:end]).strip()
        if not section_text or not _has_meaningful_content(section_text):
            continue

        if len(section_text) <= max_chunk_size:
            chunks.append(
                Chunk(
                    content=section_text,
                    source=source,
                    heading=heading,
                    heading_level=level,
                    start_line=start + 1,
                    end_line=end,
                )
            )
        else:
            chunks.extend(
                _split_large_section(
                    lines[start:end],
                    source=source,
                    heading=heading,
                    heading_level=level,
                    base_line=start,
                    max_size=max_chunk_size,
                    overlap=overlap_lines,
                )
            )

    return chunks


def _split_large_section(
    lines: list[str],
    *,
    source: str,
    heading: str,
    heading_level: int,
    base_line: int,
    max_size: int,
    overlap: int,
) -> list[Chunk]:
    """Split a large section into smaller chunks.

    Split priority: paragraph boundary > line boundary > sentence/char boundary.
    """
    chunks: list[Chunk] = []
    current_lines: list[str] = []
    current_start = 0

    def _emit(content: str, start_line: int, end_line: int) -> None:
        if content:
            chunks.append(
                Chunk(
                    content=content,
                    source=source,
                    heading=heading,
                    heading_level=heading_level,
                    start_line=start_line,
                    end_line=end_line,
                )
            )

    def _emit_bounded(content: str, start_line: int, end_line: int) -> None:
        content = content.strip()
        if not content:
            return
        if len(content) > max_size:
            for part in _split_long_text(content, max_size):
                _emit(part.strip(), start_line, end_line)
        else:
            _emit(content, start_line, end_line)

    for i, line in enumerate(lines):
        current_lines.append(line)
        text = "\n".join(current_lines)

        is_paragraph_break = line.strip() == "" and i + 1 < len(lines)
        is_last_line = i == len(lines) - 1

        if len(text) >= max_size and is_paragraph_break:
            _emit_bounded(text, base_line + current_start + 1, base_line + i + 1)
            overlap_start = max(0, len(current_lines) - overlap)
            current_lines = current_lines[overlap_start:]
            current_start = i + 1 - len(current_lines)
            continue

        if len(text) >= max_size and not is_paragraph_break and len(current_lines) > 1:
            current_lines.pop()
            content = "\n".join(current_lines).strip()
            _emit_bounded(content, base_line + current_start + 1, base_line + i)
            overlap_start = max(0, len(current_lines) - overlap)
            current_lines = current_lines[overlap_start:]
            current_lines.append(line)
            current_start = i - len(current_lines) + 1
            continue

        if len(text) >= max_size and len(current_lines) == 1:
            sub_chunks = _split_long_text(text, max_size)
            for part in sub_chunks:
                _emit(part.strip(), base_line + current_start + 1, base_line + i + 1)
            current_lines = []
            current_start = i + 1
            continue

        if is_last_line:
            _emit_bounded(text, base_line + current_start + 1, base_line + i + 1)
            current_lines = []

    if current_lines:
        remaining = "\n".join(current_lines).strip()
        if remaining:
            end_line = base_line + len(lines)
            start_line = base_line + current_start + 1
            _emit_bounded(remaining, start_line, end_line)

    return chunks


# Sentence-ending punctuation for splitting long text without line breaks.
# CJK punctuation (fullwidth stop/exclaim/question/semicolon + ellipsis)
# always acts as a boundary. ASCII punctuation (.!?;) only counts when
# followed by whitespace, end-of-string, or a CJK character -- so
# `user@example.com`, `path/to/file.py`, `http://foo.bar`, and `v1.2.3`
# are not split mid-token.
_SENTENCE_END_RE = re.compile(
    r"(?:……|…|[。！？；]\s*|[.!?;](?=\s|$|[一-鿿぀-ヿ가-힯])\s*)"
)


def _split_long_text(text: str, max_size: int) -> list[str]:
    """Split a long string that has no line breaks into pieces ≤ *max_size*.

    Prefers splitting at sentence boundaries; falls back to character position.
    """
    parts: list[str] = []
    while len(text) > max_size:
        best = -1
        for m in _SENTENCE_END_RE.finditer(text, 0, max_size):
            best = m.end()
        if best > 0:
            parts.append(text[:best])
            text = text[best:]
        else:
            parts.append(text[:max_size])
            text = text[max_size:]
    if text:
        parts.append(text)
    return parts


__all__ = [
    "Chunk",
    "chunk_markdown",
    "clean_content_for_embedding",
    "compute_chunk_id",
]
