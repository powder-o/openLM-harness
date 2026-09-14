"""Study endpoints (SPEC §9): flashcards, quiz, guides, learning path, mastery, and notes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import config, db
from ..study_tools import flashcards, guide, mastery, path, quiz

router = APIRouter(prefix="/api", tags=["study"])


def _require_notebook(conn, notebook_id: int) -> dict:
    nb = db.one(conn, "SELECT * FROM notebooks WHERE id = ?", (notebook_id,))
    if nb is None:
        raise HTTPException(status_code=404, detail="notebook not found")
    return nb


# ---------------------------------------------------------------------------------------------
# Flashcards
# ---------------------------------------------------------------------------------------------

@router.post("/study/flashcards/generate")
def generate_flashcards(body: dict = Body(...), conn=Depends(db.get_conn)):
    notebook_id = body["notebook_id"]
    _require_notebook(conn, notebook_id)
    return flashcards.generate(conn, notebook_id, body["scope"], body.get("count", 10))


@router.get("/study/flashcards")
def list_flashcards(notebook_id: int, due_only: bool = False, conn=Depends(db.get_conn)):
    return flashcards.list_cards(conn, notebook_id, due_only)


@router.post("/study/flashcards/{card_id}/review")
def review_flashcard(card_id: int, body: dict = Body(...), conn=Depends(db.get_conn)):
    try:
        return flashcards.review(conn, card_id, int(body["grade"]))
    except KeyError:
        raise HTTPException(status_code=404, detail="flashcard not found")


@router.delete("/study/flashcards/{card_id}")
def delete_flashcard(card_id: int, conn=Depends(db.get_conn)):
    flashcards.delete(conn, card_id)
    return {"ok": True}


# ---------------------------------------------------------------------------------------------
# Quiz
# ---------------------------------------------------------------------------------------------

@router.post("/study/quiz/generate")
def generate_quiz(body: dict = Body(...), conn=Depends(db.get_conn)):
    notebook_id = body["notebook_id"]
    _require_notebook(conn, notebook_id)
    return quiz.generate(conn, notebook_id, body["scope"], body.get("count", 5))


@router.post("/study/quiz/{quiz_id}/submit")
def submit_quiz(quiz_id: int, body: dict = Body(...), conn=Depends(db.get_conn)):
    try:
        return quiz.submit(conn, quiz_id, body.get("answers", []))
    except KeyError:
        raise HTTPException(status_code=404, detail="quiz not found")


# ---------------------------------------------------------------------------------------------
# Guides
# ---------------------------------------------------------------------------------------------

@router.post("/study/guides")
def generate_guide(body: dict = Body(...), conn=Depends(db.get_conn)):
    notebook_id = body["notebook_id"]
    _require_notebook(conn, notebook_id)
    return guide.generate(conn, notebook_id, body["scope"])


@router.get("/study/guides")
def list_guides(notebook_id: int, conn=Depends(db.get_conn)):
    return guide.list_guides(conn, notebook_id)


# ---------------------------------------------------------------------------------------------
# Path + mastery
# ---------------------------------------------------------------------------------------------

@router.get("/study/path")
def get_path(concept_id: int, conn=Depends(db.get_conn)):
    try:
        return path.learning_path(conn, concept_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="concept not found")


@router.get("/study/mastery")
def get_mastery(notebook_id: int, conn=Depends(db.get_conn)):
    return mastery.get_mastery(conn, notebook_id)


# ---------------------------------------------------------------------------------------------
# Notes (documents of kind "note", saved as markdown, queued for ingestion like any other upload)
# ---------------------------------------------------------------------------------------------

def _enqueue(document_id: int) -> None:
    try:
        from study.ingest.worker import enqueue
    except ModuleNotFoundError:
        return
    enqueue(document_id)


def _note_path(document_id: int) -> Path:
    return config.document_dir(document_id) / "original.md"


@router.post("/notebooks/{notebook_id}/notes", status_code=201)
def create_note(notebook_id: int, body: dict = Body(...), conn=Depends(db.get_conn)):
    _require_notebook(conn, notebook_id)
    title = body.get("title") or "Untitled note"
    content = body.get("content_md", "")
    cur = conn.execute(
        "INSERT INTO documents (notebook_id, title, kind, source_name, status) VALUES (?, ?, 'note', ?, 'queued')",
        (notebook_id, title, title),
    )
    document_id = cur.lastrowid
    _note_path(document_id).write_text(content, encoding="utf-8")
    conn.execute(
        "UPDATE documents SET file_path = ? WHERE id = ?", (f"files/{document_id}/original.md", document_id)
    )
    _enqueue(document_id)
    return db.one(conn, "SELECT * FROM documents WHERE id = ?", (document_id,))


@router.get("/documents/{document_id}/note")
def get_note(document_id: int, conn=Depends(db.get_conn)):
    doc = db.one(conn, "SELECT * FROM documents WHERE id = ? AND kind = 'note'", (document_id,))
    if doc is None:
        raise HTTPException(status_code=404, detail="note not found")
    p = _note_path(document_id)
    content = p.read_text(encoding="utf-8") if p.exists() else ""
    return {"title": doc["title"], "content_md": content}


@router.put("/documents/{document_id}/note")
def update_note(document_id: int, body: dict = Body(...), conn=Depends(db.get_conn)):
    doc = db.one(conn, "SELECT * FROM documents WHERE id = ? AND kind = 'note'", (document_id,))
    if doc is None:
        raise HTTPException(status_code=404, detail="note not found")
    title = body.get("title", doc["title"])
    content = body.get("content_md", "")
    _note_path(document_id).write_text(content, encoding="utf-8")
    conn.execute(
        "UPDATE documents SET title = ?, status = 'queued', status_detail = '', error = NULL WHERE id = ?",
        (title, document_id),
    )
    _enqueue(document_id)
    return db.one(conn, "SELECT * FROM documents WHERE id = ?", (document_id,))
