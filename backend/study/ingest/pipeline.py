"""Ingestion pipeline orchestration: parsing -> describing -> indexing -> graphing -> ready (SPEC §6)."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .. import config, db
from ..graph import after_document_indexed
from ..index import embed
from . import chunk, figures, parse_docling, parse_text

log = logging.getLogger("study.ingest.pipeline")

_DOCLING_KINDS = {"pdf", "docx", "html", "web"}
_MARKDOWN_KINDS = {"md", "markdown", "note"}
_TEXT_KINDS = {"txt"}


def _set_status(conn, document_id: int, status: str, detail: str = "") -> None:
    conn.execute(
        "UPDATE documents SET status=?, status_detail=?, updated_at=? WHERE id=?",
        (status, detail, db.now_iso(), document_id),
    )


def _clear_old(conn, document_id: int) -> None:
    with db.transaction(conn):
        conn.execute("DELETE FROM blocks WHERE document_id=?", (document_id,))
        conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))


def _parse(doc: dict, settings: dict, source_path: Path | None, tmp_dir: Path):
    kind = doc["kind"]
    if kind in _DOCLING_KINDS:
        if source_path is None:
            raise ValueError(f"document {doc['id']} has no file_path to parse")
        return parse_docling.parse_file(source_path, kind, settings, tmp_dir)
    if kind in _MARKDOWN_KINDS:
        if source_path is None:
            raise ValueError(f"document {doc['id']} has no file_path to parse")
        return parse_text.parse_file(source_path, "markdown")
    if kind in _TEXT_KINDS:
        if source_path is None:
            raise ValueError(f"document {doc['id']} has no file_path to parse")
        return parse_text.parse_file(source_path, "text")
    raise ValueError(f"unsupported document kind: {kind!r}")


def _insert_blocks(conn, document_id: int, blocks: list[dict[str, Any]]) -> None:
    last_heading_id: int | None = None
    with db.transaction(conn):
        for seq, b in enumerate(blocks):
            section_id = last_heading_id
            cur = conn.execute(
                "INSERT INTO blocks (document_id, seq, type, text, level, page, bbox_json, section_id,"
                " image_path, table_html, caption, label) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    seq,
                    b["type"],
                    b.get("text") or "",
                    b.get("level"),
                    b.get("page"),
                    json.dumps(b["bbox"]) if b.get("bbox") is not None else None,
                    section_id,
                    b.get("image_path"),
                    b.get("table_html"),
                    b.get("caption"),
                    b.get("label"),
                ),
            )
            if b["type"] == "heading":
                last_heading_id = cur.lastrowid


def _insert_chunks(conn, document_id: int, notebook_id: int, chunk_dicts: list[dict[str, Any]]) -> None:
    if not chunk_dicts:
        return
    texts = [c["heading_path"] + "\n" + c["text"] for c in chunk_dicts]
    vecs = embed.embed_texts(texts)
    with db.transaction(conn):
        for i, (c, vec) in enumerate(zip(chunk_dicts, vecs)):
            conn.execute(
                "INSERT INTO chunks (document_id, notebook_id, seq, kind, text, heading_path, block_ids_json,"
                " section_id, page_start, page_end, token_count, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    notebook_id,
                    i,
                    c["kind"],
                    c["text"],
                    c["heading_path"],
                    json.dumps(c["block_ids"]),
                    c.get("section_id"),
                    c.get("page_start"),
                    c.get("page_end"),
                    c.get("token_count", 0),
                    embed.to_blob(vec),
                ),
            )


def run(document_id: int) -> None:
    """Runs the full pipeline on its own connection. Never raises: failures set status='error'."""
    conn = db.connect()
    try:
        _run(conn, document_id)
    except Exception as exc:
        log.exception("ingest pipeline failed for document %s", document_id)
        try:
            conn.execute(
                "UPDATE documents SET status='error', status_detail=?, error=?, updated_at=? WHERE id=?",
                ("failed", f"{type(exc).__name__}: {exc}", db.now_iso(), document_id),
            )
        except Exception:
            log.exception("failed to record error status for document %s", document_id)
    finally:
        conn.close()


def _run(conn, document_id: int) -> None:
    doc = db.one(conn, "SELECT * FROM documents WHERE id=?", (document_id,))
    if doc is None:
        return
    nb = db.one(conn, "SELECT * FROM notebooks WHERE id=?", (doc["notebook_id"],))
    settings = config.notebook_settings(nb["settings_json"] if nb else None)
    source_path = (config.data_dir() / doc["file_path"]) if doc["file_path"] else None

    _set_status(conn, document_id, "parsing", "")
    _clear_old(conn, document_id)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"study-parse-{document_id}-"))
    try:
        parsed = _parse(doc, settings, source_path, tmp_dir)

        conn.execute(
            "UPDATE documents SET num_pages=?, page_sizes_json=?, updated_at=? WHERE id=?",
            (
                parsed.num_pages,
                json.dumps(parsed.page_sizes) if parsed.page_sizes is not None else None,
                db.now_iso(),
                document_id,
            ),
        )
        _insert_blocks(conn, document_id, parsed.blocks)

        _set_status(conn, document_id, "describing", "")
        figures.process_figures(
            conn,
            document_id,
            doc["kind"],
            source_path,
            settings,
            progress=lambda d: _set_status(conn, document_id, "describing", d),
        )

        _set_status(conn, document_id, "indexing", "")
        block_rows = db.rows(conn, "SELECT * FROM blocks WHERE document_id=? ORDER BY seq", (document_id,))
        chunk_dicts = chunk.chunk_blocks(doc["title"], block_rows, settings)
        _insert_chunks(conn, document_id, doc["notebook_id"], chunk_dicts)
        figures.link_figures(conn, document_id)

        _set_status(conn, document_id, "graphing", "")
        after_document_indexed(conn, document_id, progress=lambda d: _set_status(conn, document_id, "graphing", d))

        _set_status(conn, document_id, "ready", "")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
