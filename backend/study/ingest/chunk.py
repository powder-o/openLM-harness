"""Blocks -> chunks (SPEC §6 step 3). Setting-driven, block-level overlap, figure/table = own chunk."""
from __future__ import annotations

from typing import Any


def estimate_tokens(text: str) -> int:
    """Token estimate ~= words * 1.3."""
    if not text:
        return 0
    return max(1, round(len(text.split()) * 1.3))


def _figure_chunk_text(b: dict) -> str:
    parts = [b.get("label"), b.get("caption")]
    if b["type"] == "table":
        parts.append(b.get("text"))
    else:
        parts.append(b.get("description"))
    return "\n".join(p for p in parts if p)


def _min_page(blocks: list[dict]) -> int | None:
    pages = [b["page"] for b in blocks if b.get("page") is not None]
    return min(pages) if pages else None


def _max_page(blocks: list[dict]) -> int | None:
    pages = [b["page"] for b in blocks if b.get("page") is not None]
    return max(pages) if pages else None


def chunk_blocks(doc_title: str, blocks: list[dict], settings: dict) -> list[dict[str, Any]]:
    """blocks: ordered rows with at least id, type, text, level, page, section_id (already inserted,
    so ids/section_id are known). Returns chunk dicts: kind, text, heading_path, block_ids, section_id,
    page_start, page_end, token_count."""
    max_tokens = int(settings.get("chunk_max_tokens", 350))
    overlap_tokens = int(settings.get("chunk_overlap_tokens", 40))

    chunks: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str]] = []
    cur_blocks: list[dict] = []
    cur_tokens = 0

    def heading_path() -> str:
        return " > ".join([doc_title] + [t for _, t in heading_stack])

    def flush(carry: bool) -> None:
        nonlocal cur_blocks, cur_tokens
        if not cur_blocks:
            return
        text = "\n\n".join(b["text"] for b in cur_blocks if b.get("text"))
        if text.strip():
            chunks.append(
                {
                    "kind": "text",
                    "text": text,
                    "heading_path": heading_path(),
                    "block_ids": [b["id"] for b in cur_blocks],
                    "section_id": cur_blocks[0].get("section_id"),
                    "page_start": _min_page(cur_blocks),
                    "page_end": _max_page(cur_blocks),
                    "token_count": estimate_tokens(text),
                }
            )
        last = cur_blocks[-1]
        last_tokens = estimate_tokens(last.get("text", ""))
        if carry and last_tokens <= overlap_tokens:
            cur_blocks = [last]
            cur_tokens = last_tokens
        else:
            cur_blocks = []
            cur_tokens = 0

    for b in blocks:
        btype = b["type"]
        if btype == "heading":
            level = b.get("level") or 1
            if level <= 2:
                flush(carry=False)  # flush with the OLD heading stack before it changes
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, b["text"]))
            if level <= 2:
                continue
            # deeper headings don't force a break; fall through and get folded into the chunk as content

        if btype in ("figure", "table"):
            flush(carry=False)
            text = _figure_chunk_text(b)
            chunks.append(
                {
                    "kind": btype,
                    "text": text,
                    "heading_path": heading_path(),
                    "block_ids": [b["id"]],
                    "section_id": b.get("section_id"),
                    "page_start": b.get("page"),
                    "page_end": b.get("page"),
                    "token_count": estimate_tokens(text),
                }
            )
            continue

        tok = estimate_tokens(b.get("text", ""))
        if cur_blocks and cur_tokens + tok > max_tokens:
            flush(carry=True)
        cur_blocks.append(b)
        cur_tokens += tok

    flush(carry=False)
    return chunks
