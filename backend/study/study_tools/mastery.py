"""Concept mastery (SPEC §9): a 0..1 score per concept, blended 0.3 into the prior on each signal."""
from __future__ import annotations

from .. import db

BLEND = 0.3


def _blend(conn, concept_id: int, sample: float) -> None:
    row = db.one(conn, "SELECT score FROM concept_mastery WHERE concept_id = ?", (concept_id,))
    prior = row["score"] if row else 0.0
    new_score = prior * (1 - BLEND) + sample * BLEND
    conn.execute(
        "INSERT INTO concept_mastery (concept_id, score, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(concept_id) DO UPDATE SET score = excluded.score, updated_at = excluded.updated_at",
        (concept_id, new_score, db.now_iso()),
    )


def update_from_grade(conn, concept_id: int, grade: int) -> None:
    """Flashcard review grade (0 again / 1 good / 2 easy) -> sample = grade / 2."""
    _blend(conn, concept_id, grade / 2.0)


def update_from_quiz(conn, concept_id: int, correct: bool) -> None:
    _blend(conn, concept_id, 1.0 if correct else 0.0)


def get_mastery(conn, notebook_id: int) -> dict[str, float]:
    rows = db.rows(
        conn,
        "SELECT c.id, COALESCE(cm.score, 0.0) AS score FROM concepts c"
        " LEFT JOIN concept_mastery cm ON cm.concept_id = c.id WHERE c.notebook_id = ?",
        (notebook_id,),
    )
    return {str(r["id"]): r["score"] for r in rows}
