"""Ingest hooks for the graph tiers (SPEC §8).

Contract: the ingest pipeline calls these; they must never raise. A missing API key or an LLM
error is logged and swallowed so the structure tier (deterministic, no LLM) is always queryable.
"""
from __future__ import annotations

import logging
from typing import Callable

from .. import config, db, llm
from . import merge, topics
from .concepts import extract_for_document

log = logging.getLogger("study.graph")


def after_document_indexed(conn, document_id: int, progress: Callable[[str], None] = lambda _d: None) -> None:
    doc = db.one(conn, "SELECT notebook_id FROM documents WHERE id = ?", (document_id,))
    if not doc:
        return
    notebook_id = doc["notebook_id"]
    try:
        nb = db.one(conn, "SELECT settings_json FROM notebooks WHERE id = ?", (notebook_id,))
        settings = config.notebook_settings(nb["settings_json"] if nb else None)
        if settings.get("extract_concepts") and llm.get_llm().available():
            result = extract_for_document(conn, document_id, progress)
            merge.apply_extraction(conn, notebook_id, result)
            topics.recompute_topics(conn, notebook_id)
    except Exception:
        log.exception(
            "concept extraction failed for document %d; structure tier is still available", document_id
        )
    db.mark_graph_dirty(conn, notebook_id)


def after_document_deleted(conn, notebook_id: int) -> None:
    try:
        orphans = db.rows(
            conn,
            "SELECT id FROM concepts WHERE notebook_id = ? AND id NOT IN (SELECT concept_id FROM concept_mentions)",
            (notebook_id,),
        )
        for o in orphans:
            conn.execute("DELETE FROM concepts WHERE id = ?", (o["id"],))
        topics.recompute_topics(conn, notebook_id)
    except Exception:
        log.exception("post-deletion graph cleanup failed for notebook %d", notebook_id)
    db.mark_graph_dirty(conn, notebook_id)
