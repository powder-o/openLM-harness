"""Builds an LLM-ready context (a list of chunks, capped ~6000 tokens) for a study-tool scope:
notebook / document / section / concept (SPEC §9).
"""
from __future__ import annotations

from .. import db, llm

TOKEN_BUDGET = 6000


def _token_estimate(chunk: dict) -> int:
    return chunk.get("token_count") or max(1, len(chunk["text"]) // 4)


def _cap(chunks: list[dict]) -> list[dict]:
    out: list[dict] = []
    total = 0
    for c in chunks:
        t = _token_estimate(c)
        if out and total + t > TOKEN_BUDGET:
            break
        out.append(c)
        total += t
    return out


def _notebook_chunks(conn, notebook_id: int) -> list[dict]:
    """A spread of chunks across documents, preferring chunks with the most concept mentions."""
    rows = db.rows(
        conn,
        "SELECT ch.*, (SELECT COUNT(*) FROM concept_mentions cm WHERE cm.chunk_id = ch.id) AS mention_count"
        " FROM chunks ch WHERE ch.notebook_id = ? ORDER BY ch.document_id, ch.seq",
        (notebook_id,),
    )
    by_doc: dict[int, list[dict]] = {}
    for r in rows:
        by_doc.setdefault(r["document_id"], []).append(r)
    for doc_rows in by_doc.values():
        doc_rows.sort(key=lambda r: -r["mention_count"])

    ordered: list[dict] = []
    doc_ids = list(by_doc.keys())
    i = 0
    while doc_ids and any(by_doc[d] for d in doc_ids):
        doc_id = doc_ids[i % len(doc_ids)]
        if by_doc[doc_id]:
            ordered.append(by_doc[doc_id].pop(0))
        i += 1
    return _cap(ordered)


def _document_chunks(conn, document_id: int) -> list[dict]:
    return _cap(db.rows(conn, "SELECT * FROM chunks WHERE document_id = ? ORDER BY seq", (document_id,)))


def _section_and_descendants(conn, root_id: int, document_id: int) -> list[int]:
    headings = db.rows(
        conn, "SELECT id, section_id FROM blocks WHERE document_id = ? AND type = 'heading'", (document_id,)
    )
    children: dict[int, list[int]] = {}
    for h in headings:
        if h["section_id"] is not None:
            children.setdefault(h["section_id"], []).append(h["id"])
    out = [root_id]
    stack = [root_id]
    while stack:
        cur = stack.pop()
        for ch in children.get(cur, []):
            out.append(ch)
            stack.append(ch)
    return out


def _section_chunks(conn, section_block_id: int) -> list[dict]:
    block = db.one(conn, "SELECT document_id FROM blocks WHERE id = ?", (section_block_id,))
    if block is None:
        return []
    document_id = block["document_id"]
    ids = _section_and_descendants(conn, section_block_id, document_id)
    placeholders = ",".join("?" * len(ids))
    rows = db.rows(
        conn, f"SELECT * FROM chunks WHERE document_id = ? AND section_id IN ({placeholders}) ORDER BY seq",
        [document_id, *ids],
    )
    return _cap(rows)


def _concept_chunks(conn, concept_id: int) -> list[dict]:
    rows = db.rows(
        conn,
        "SELECT ch.* FROM chunks ch JOIN concept_mentions cm ON cm.chunk_id = ch.id"
        " WHERE cm.concept_id = ? ORDER BY ch.document_id, ch.seq",
        (concept_id,),
    )
    return _cap(rows)


def resolve_scope_chunks(conn, scope: dict) -> list[dict]:
    kind = scope.get("kind")
    scope_id = scope.get("id")
    if kind == "notebook":
        return _notebook_chunks(conn, scope_id)
    if kind == "document":
        return _document_chunks(conn, scope_id)
    if kind == "section":
        return _section_chunks(conn, scope_id)
    if kind == "concept":
        return _concept_chunks(conn, scope_id)
    raise ValueError(f"unknown scope kind: {kind!r}")


def build_context_text(chunks: list[dict]) -> str:
    """Wrap each chunk with a `[c:ID]` label, inside an untrusted_source block."""
    parts = []
    for c in chunks:
        header = f"[c:{c['id']}] {c.get('heading_path', '')}".strip()
        parts.append(llm.wrap_untrusted(f"chunk:{c['id']}", f"{header}\n{c['text']}"))
    return "\n\n".join(parts)
