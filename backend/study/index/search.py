"""Hybrid search: FTS5 bm25 + cosine similarity, fused with RRF (SPEC §7)."""
from __future__ import annotations

import re

import numpy as np

from .. import db
from . import embed

RRF_K = 60
FTS_TOP = 30
COSINE_TOP = 30


def parse_scope(scope: str) -> tuple[str, int]:
    """"notebook:1" -> ("notebook", 1)."""
    kind, sep, raw_id = scope.partition(":")
    if not sep or kind not in ("notebook", "archive") or not raw_id.isdigit():
        raise ValueError(f"invalid scope: {scope!r}")
    return kind, int(raw_id)


def scope_notebook_ids(conn, kind: str, id: int) -> list[int]:
    if kind == "notebook":
        return [id]
    if kind == "archive":
        return [r["id"] for r in db.rows(conn, "SELECT id FROM notebooks WHERE archive_id=?", (id,))]
    raise ValueError(f"invalid scope kind: {kind!r}")


def _fts_match(query: str) -> str | None:
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def _fts_hits(conn, notebook_ids: list[int], query: str) -> list[int]:
    match = _fts_match(query)
    if match is None or not notebook_ids:
        return []
    placeholders = ",".join("?" * len(notebook_ids))
    sql = (
        "SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
        f"WHERE chunks_fts MATCH ? AND c.notebook_id IN ({placeholders}) "
        "ORDER BY bm25(chunks_fts) LIMIT ?"
    )
    try:
        rows_ = conn.execute(sql, [match, *notebook_ids, FTS_TOP]).fetchall()
    except Exception:
        return []
    return [r[0] for r in rows_]


def _cosine_hits(conn, notebook_ids: list[int], query: str) -> list[int]:
    if not notebook_ids:
        return []
    placeholders = ",".join("?" * len(notebook_ids))
    rows_ = db.rows(
        conn,
        f"SELECT id, embedding FROM chunks WHERE notebook_id IN ({placeholders}) AND embedding IS NOT NULL",
        notebook_ids,
    )
    if not rows_:
        return []
    qvec = embed.embed_query(query)
    scored: list[tuple[float, int]] = []
    for r in rows_:
        vec = embed.from_blob(r["embedding"])
        if vec is None or vec.shape[0] != qvec.shape[0]:
            continue
        scored.append((float(np.dot(qvec, vec)), r["id"]))
    scored.sort(key=lambda t: -t[0])
    return [cid for _, cid in scored[:COSINE_TOP]]


def _rrf_fuse(*ranked_lists: list[int]) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, cid in enumerate(ranked, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda t: -t[1])


def _figures_for_chunk(conn, chunk_id: int) -> list[dict]:
    rows_ = db.rows(
        conn,
        "SELECT b.id AS block_id, b.label, b.caption FROM figure_links fl "
        "JOIN blocks b ON b.id = fl.figure_block_id WHERE fl.chunk_id = ? ORDER BY fl.score DESC",
        (chunk_id,),
    )
    return [{"block_id": r["block_id"], "label": r["label"], "caption": r["caption"]} for r in rows_]


def hybrid_search(conn, scope: str, query: str, k: int = 8) -> list[dict]:
    kind, sid = parse_scope(scope)
    notebook_ids = scope_notebook_ids(conn, kind, sid)
    if not notebook_ids or not (query or "").strip():
        return []

    fts_ranked = _fts_hits(conn, notebook_ids, query)
    cos_ranked = _cosine_hits(conn, notebook_ids, query)
    fused = _rrf_fuse(fts_ranked, cos_ranked)[:k]

    hits: list[dict] = []
    for chunk_id, score in fused:
        c = db.one(
            conn,
            "SELECT c.*, d.title AS document_title FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE c.id = ?",
            (chunk_id,),
        )
        if c is None:
            continue
        hits.append(
            {
                "chunk_id": c["id"],
                "document_id": c["document_id"],
                "document_title": c["document_title"],
                "notebook_id": c["notebook_id"],
                "kind": c["kind"],
                "page_start": c["page_start"],
                "page_end": c["page_end"],
                "heading_path": c["heading_path"],
                "text": c["text"],
                "score": score,
                "figures": _figures_for_chunk(conn, chunk_id),
            }
        )
    return hits


def chunk_in_scope(conn, scope: str, chunk_id: int) -> bool:
    kind, sid = parse_scope(scope)
    notebook_ids = set(scope_notebook_ids(conn, kind, sid))
    row = db.one(conn, "SELECT notebook_id FROM chunks WHERE id=?", (chunk_id,))
    return bool(row) and row["notebook_id"] in notebook_ids
