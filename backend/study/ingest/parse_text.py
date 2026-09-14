"""Markdown / plain-text parser -> canonical blocks. No pages, no bboxes (SPEC §6, §1)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ATX_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class ParseResult:
    title: str
    num_pages: int | None
    page_sizes: list[list[float]] | None
    blocks: list[dict[str, Any]]


def _block(type_: str, text: str, level: int | None = None) -> dict[str, Any]:
    return {
        "type": type_,
        "text": text,
        "level": level,
        "page": None,
        "bbox": None,
        "image_path": None,
        "table_html": None,
        "caption": None,
        "label": None,
    }


def parse_markdown(text: str) -> list[dict[str, Any]]:
    """ATX headings, paragraphs, list items, fenced code."""
    blocks: list[dict[str, Any]] = []
    lines = text.splitlines()
    para_buf: list[str] = []

    def flush_para() -> None:
        if para_buf:
            joined = " ".join(l.strip() for l in para_buf).strip()
            if joined:
                blocks.append(_block("paragraph", joined))
            para_buf.clear()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _ATX_RE.match(line)
        if m:
            flush_para()
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            if heading_text:
                blocks.append(_block("heading", heading_text, level=level))
            i += 1
            continue

        fm = _FENCE_RE.match(line)
        if fm:
            flush_para()
            fence = fm.group(1)
            i += 1
            code_lines: list[str] = []
            while i < n and not lines[i].strip().startswith(fence):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence (or EOF)
            blocks.append(_block("code", "\n".join(code_lines)))
            continue

        lm = _LIST_RE.match(line)
        if lm:
            flush_para()
            item_text = lm.group(1).strip()
            if item_text:
                blocks.append(_block("list_item", item_text))
            i += 1
            continue

        if not line.strip():
            flush_para()
            i += 1
            continue

        para_buf.append(line)
        i += 1

    flush_para()
    return blocks


def parse_plain_text(text: str) -> list[dict[str, Any]]:
    """Blank-line-separated paragraphs, no headings."""
    blocks: list[dict[str, Any]] = []
    for para in re.split(r"\n\s*\n", text):
        joined = " ".join(para.split())
        if joined:
            blocks.append(_block("paragraph", joined))
    return blocks


def _title_from_blocks(blocks: list[dict[str, Any]], fallback: str) -> str:
    for b in blocks:
        if b["type"] == "heading" and (b["level"] or 1) == 1:
            return b["text"]
    return fallback


def parse_file(path: str | Path, mode: str) -> ParseResult:
    """mode: 'markdown' | 'text'."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    blocks = parse_markdown(text) if mode == "markdown" else parse_plain_text(text)
    title = _title_from_blocks(blocks, p.stem)
    return ParseResult(title=title, num_pages=None, page_sizes=None, blocks=blocks)
