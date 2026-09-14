"""Flashcard generation + SM-2-lite review (SPEC §9)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .. import db, llm
from . import context, mastery

_CARD_COLUMNS = "id, notebook_id, concept_id, chunk_id, front, back, due_at, reps, interval_days, ease"

_SYSTEM = f"""\
You write flashcards (front/back) from study material for spaced-repetition review.
Output ONLY valid JSON: {{"cards": [{{"front": "...", "back": "...", "concept": "concept name or null",
"chunk_id": 12}}]}}. Keep fronts short (a question or term); backs concise (1-3 sentences).
{llm.UNTRUSTED_RULE}
"""


def require_llm() -> None:
    if not llm.get_llm().available():
        raise HTTPException(status_code=409, detail="DeepSeek API key is not configured")


def concept_name_map(conn, notebook_id: int) -> dict[str, int]:
    return {
        r["name"].strip().lower(): r["id"]
        for r in db.rows(conn, "SELECT id, name FROM concepts WHERE notebook_id = ?", (notebook_id,))
    }


def concept_for_chunk(conn, notebook_id: int, chunk_id: int | None) -> int | None:
    if chunk_id is None:
        return None
    row = db.one(
        conn,
        "SELECT cm.concept_id FROM concept_mentions cm JOIN concepts c ON c.id = cm.concept_id"
        " WHERE cm.chunk_id = ? AND c.notebook_id = ? LIMIT 1",
        (chunk_id, notebook_id),
    )
    return row["concept_id"] if row else None


def generate(conn, notebook_id: int, scope: dict, count: int = 10) -> list[dict]:
    require_llm()
    chunks = context.resolve_scope_chunks(conn, scope)
    if not chunks:
        return []
    valid_ids = {c["id"] for c in chunks}
    user = f"Generate up to {count} flashcards from this material.\n\n" + context.build_context_text(chunks)
    llm_client = llm.get_llm()
    out = llm_client.chat_json(task="flashcards", system=_SYSTEM, user=user, max_tokens=3000)
    raw_cards = out.get("cards", []) if isinstance(out, dict) else []

    name_map = concept_name_map(conn, notebook_id)
    created: list[dict] = []
    for card in raw_cards[:count]:
        if not isinstance(card, dict):
            continue
        front = str(card.get("front") or "").strip()
        back = str(card.get("back") or "").strip()
        if not front or not back:
            continue
        chunk_id = card.get("chunk_id")
        chunk_id = chunk_id if chunk_id in valid_ids else chunks[0]["id"]
        concept_id = name_map.get(str(card.get("concept") or "").strip().lower())
        if concept_id is None:
            concept_id = concept_for_chunk(conn, notebook_id, chunk_id)
        cur = conn.execute(
            "INSERT INTO flashcards (notebook_id, concept_id, chunk_id, front, back) VALUES (?,?,?,?,?)",
            (notebook_id, concept_id, chunk_id, front, back),
        )
        created.append(_row(conn, cur.lastrowid))
    return created


def list_cards(conn, notebook_id: int, due_only: bool = False) -> list[dict]:
    sql = f"SELECT {_CARD_COLUMNS} FROM flashcards WHERE notebook_id = ?"
    params: list = [notebook_id]
    if due_only:
        sql += " AND due_at <= ?"
        params.append(db.now_iso())
    sql += " ORDER BY due_at"
    return db.rows(conn, sql, params)


def _row(conn, card_id: int) -> dict:
    return db.one(conn, f"SELECT {_CARD_COLUMNS} FROM flashcards WHERE id = ?", (card_id,))


def _iso_in(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def review(conn, card_id: int, grade: int) -> dict:
    card = db.one(conn, "SELECT * FROM flashcards WHERE id = ?", (card_id,))
    if card is None:
        raise KeyError(card_id)

    ease = card["ease"]
    interval = card["interval_days"]
    if grade == 0:  # again
        interval = 0.0
        ease = max(1.3, ease - 0.2)
        due_at = _iso_in(timedelta(minutes=10))
    elif grade == 1:  # good
        interval = max(1.0, interval * ease)
        due_at = _iso_in(timedelta(days=interval))
    else:  # easy
        interval = interval * ease * 1.3
        ease = ease + 0.15
        due_at = _iso_in(timedelta(days=interval))

    conn.execute(
        "UPDATE flashcards SET ease = ?, interval_days = ?, reps = reps + 1, due_at = ?, last_grade = ?"
        " WHERE id = ?",
        (ease, interval, due_at, grade, card_id),
    )
    if card["concept_id"]:
        mastery.update_from_grade(conn, card["concept_id"], grade)
    return _row(conn, card_id)


def delete(conn, card_id: int) -> None:
    conn.execute("DELETE FROM flashcards WHERE id = ?", (card_id,))


@llm.fake_handler("flashcards")
def _fake_flashcards(system: str, user: str) -> dict:
    from ..graph.concepts import _phrases_in  # deterministic capitalized-phrase heuristic

    cards = []
    for m in re.finditer(r'<untrusted_source id="chunk:(\d+)">\s*(.*?)\s*</untrusted_source>', user, re.S):
        chunk_id, text = int(m.group(1)), m.group(2)
        phrases = _phrases_in(text)
        if phrases:
            name = phrases[0]
            cards.append({"front": f"What is {name}?", "back": text[:160], "concept": name, "chunk_id": chunk_id})
        else:
            cards.append({
                "front": f"Summarize chunk {chunk_id}.", "back": text[:160], "concept": None, "chunk_id": chunk_id,
            })
    return {"cards": cards}
