"""Tests for memoryd.chunking — markdown chunking + SHA256 dedup."""
from __future__ import annotations

from memoryd.chunking import (
    Chunk,
    chunk_markdown,
    clean_content_for_embedding,
    compute_chunk_id,
)


def test_simple_heading_split() -> None:
    md = """\
# Title

Some intro text.

## Section A

Content A here.

## Section B

Content B here.
"""
    chunks = chunk_markdown(md, source="test.md")
    headings = [c.heading for c in chunks]
    assert "Title" in headings
    assert "Section A" in headings
    assert "Section B" in headings


def test_preamble_without_heading() -> None:
    md = "Just some text without any heading.\n\nMore text."
    chunks = chunk_markdown(md, source="t.md")
    assert len(chunks) == 1
    assert chunks[0].heading == ""
    assert chunks[0].heading_level == 0


def test_empty_input_returns_no_chunks() -> None:
    assert chunk_markdown("", source="t.md") == []


def test_content_hash_is_deterministic_and_source_independent() -> None:
    md = "# Hello\n\nWorld"
    c1 = chunk_markdown(md, source="a.md")
    c2 = chunk_markdown(md, source="b.md")
    assert c1[0].content_hash == c2[0].content_hash
    # Hash is hex-ish (sha256 prefix) and 16 chars wide.
    assert len(c1[0].content_hash) == 16


def test_compute_chunk_id_changes_with_model() -> None:
    base_kwargs = dict(
        source="memories/foo.md",
        start_line=1,
        end_line=10,
        content_hash="abc123",
    )
    a = compute_chunk_id(model="bge-m3", **base_kwargs)
    b = compute_chunk_id(model="text-embedding-3-small", **base_kwargs)
    assert a != b
    assert len(a) == 16


def test_clean_content_strips_html_comments() -> None:
    text = "Hello\n<!-- transcript-id: abc -->\nWorld"
    cleaned = clean_content_for_embedding(text)
    assert "transcript-id" not in cleaned
    assert "Hello" in cleaned
    assert "World" in cleaned


def test_clean_content_collapses_blank_lines() -> None:
    text = "a\n\n\n\n\nb"
    cleaned = clean_content_for_embedding(text)
    # No more than one blank line between paragraphs.
    assert "\n\n\n" not in cleaned


def test_skip_empty_heading_only_sections() -> None:
    md = "## Empty\n\n## Real\n\nbody text here\n"
    chunks = chunk_markdown(md, source="t.md")
    headings = [c.heading for c in chunks]
    assert "Empty" not in headings
    assert "Real" in headings


def test_large_section_is_split() -> None:
    paragraphs = [f"Paragraph {i}. " + "x" * 200 for i in range(20)]
    md = "# Big\n\n" + "\n\n".join(paragraphs)
    chunks = chunk_markdown(md, source="t.md", max_chunk_size=500)
    assert len(chunks) > 1
    for c in chunks:
        assert c.heading == "Big"
        assert len(c.content) <= 600  # small slack for overlap


def test_cjk_sentence_split_does_not_break_paths() -> None:
    # ASCII '.' should NOT split file paths like a/b.py
    long_text = "Visit https://foo.bar/path/to/file.py for details. " + "y" * 1500
    chunks = chunk_markdown(long_text, source="t.md", max_chunk_size=800)
    # `file.py` must not appear sliced between two chunks.
    joined = "|".join(c.content for c in chunks)
    assert "file.py" in joined


def test_chunk_dataclass_is_frozen() -> None:
    c = Chunk(
        content="hi",
        source="t.md",
        heading="H",
        heading_level=1,
        start_line=1,
        end_line=1,
    )
    try:
        c.content = "bye"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Chunk should be frozen")
