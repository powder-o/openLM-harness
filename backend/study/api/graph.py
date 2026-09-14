"""Graph + concept endpoints (SPEC §8)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db
from ..graph import build

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/graph/notebook/{notebook_id}")
def get_notebook_graph(notebook_id: int, conn=Depends(db.get_conn)) -> dict:
    try:
        return build.notebook_graph(conn, notebook_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/graph/archive/{archive_id}")
def get_archive_graph(archive_id: int, conn=Depends(db.get_conn)) -> dict:
    try:
        return build.archive_graph(conn, archive_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/graph/notebook/{notebook_id}/rebuild")
def rebuild_notebook(
    notebook_id: int, reextract: bool = Query(False), conn=Depends(db.get_conn)
) -> dict:
    try:
        return build.rebuild_notebook_graph(conn, notebook_id, reextract=reextract)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/graph/archive/{archive_id}/rebuild")
def rebuild_archive(archive_id: int, conn=Depends(db.get_conn)) -> dict:
    try:
        return build.rebuild_archive_graph(conn, archive_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/concepts/{concept_id}")
def get_concept(concept_id: int, conn=Depends(db.get_conn)) -> dict:
    c = db.one(conn, "SELECT * FROM concepts WHERE id = ?", (concept_id,))
    if c is None:
        raise HTTPException(status_code=404, detail="concept not found")

    topic = None
    if c["topic_id"]:
        topic = db.one(conn, "SELECT id, label FROM topics WHERE id = ?", (c["topic_id"],))

    mastery_row = db.one(conn, "SELECT score FROM concept_mastery WHERE concept_id = ?", (concept_id,))

    mentions = db.rows(
        conn,
        "SELECT cm.chunk_id, cm.evidence, ch.document_id, d.title AS document_title, ch.page_start"
        " FROM concept_mentions cm JOIN chunks ch ON ch.id = cm.chunk_id JOIN documents d ON d.id = ch.document_id"
        " WHERE cm.concept_id = ?",
        (concept_id,),
    )

    relations = []
    for e in db.rows(conn, "SELECT * FROM concept_edges WHERE source_id = ?", (concept_id,)):
        other = db.one(conn, "SELECT id, name FROM concepts WHERE id = ?", (e["target_id"],))
        relations.append({"relation": e["relation"], "direction": "out", "confidence": e["confidence"],
                           "other": other})
    for e in db.rows(conn, "SELECT * FROM concept_edges WHERE target_id = ?", (concept_id,)):
        other = db.one(conn, "SELECT id, name FROM concepts WHERE id = ?", (e["source_id"],))
        relations.append({"relation": e["relation"], "direction": "in", "confidence": e["confidence"],
                           "other": other})

    return {
        "id": c["id"], "name": c["name"], "kind": c["kind"], "summary": c["summary"], "topic": topic,
        "mastery": mastery_row["score"] if mastery_row else 0.0, "mentions": mentions, "relations": relations,
    }
