"""Quiz generation + scoring (SPEC §9)."""
from __future__ import annotations

import json
import re

from .. import db, llm
from . import context, mastery
from .flashcards import concept_for_chunk, concept_name_map, require_llm

_SYSTEM = f"""\
You write multiple-choice quiz questions from study material.
Output ONLY valid JSON: {{"questions": [{{"question": "...", "options": ["a", "b", "c", "d"],
"answer_index": 0, "explanation": "...", "concept": "concept name or null", "chunk_id": 12}}]}}.
Always exactly 4 options; answer_index is 0-3 and indexes into options.
{llm.UNTRUSTED_RULE}
"""


def generate(conn, notebook_id: int, scope: dict, count: int = 5) -> dict:
    require_llm()
    chunks = context.resolve_scope_chunks(conn, scope)
    valid_ids = {c["id"] for c in chunks}
    user = f"Write up to {count} quiz questions from this material.\n\n" + context.build_context_text(chunks)
    llm_client = llm.get_llm()
    out = llm_client.chat_json(task="quiz", system=_SYSTEM, user=user, max_tokens=3000)
    raw_questions = out.get("questions", []) if isinstance(out, dict) else []

    name_map = concept_name_map(conn, notebook_id)
    questions: list[dict] = []
    for q in raw_questions[:count]:
        if not isinstance(q, dict):
            continue
        options = q.get("options")
        if not isinstance(options, list) or len(options) != 4:
            continue
        try:
            answer_index = int(q.get("answer_index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= answer_index < 4):
            continue
        chunk_id = q.get("chunk_id")
        chunk_id = chunk_id if chunk_id in valid_ids else (chunks[0]["id"] if chunks else None)
        concept_id = name_map.get(str(q.get("concept") or "").strip().lower())
        if concept_id is None:
            concept_id = concept_for_chunk(conn, notebook_id, chunk_id)
        questions.append({
            "question": str(q.get("question") or "").strip(),
            "options": [str(o) for o in options],
            "answer_index": answer_index,
            "explanation": str(q.get("explanation") or ""),
            "concept_id": concept_id,
            "chunk_id": chunk_id,
        })

    quiz_id = conn.execute(
        "INSERT INTO quizzes (notebook_id, scope_json, questions_json) VALUES (?,?,?)",
        (notebook_id, json.dumps(scope), json.dumps(questions)),
    ).lastrowid
    return {"id": quiz_id, "questions": [{"id": i, **q} for i, q in enumerate(questions)]}


def submit(conn, quiz_id: int, answers: list[int]) -> dict:
    quiz = db.one(conn, "SELECT * FROM quizzes WHERE id = ?", (quiz_id,))
    if quiz is None:
        raise KeyError(quiz_id)
    questions = json.loads(quiz["questions_json"] or "[]")

    results = []
    correct_count = 0
    for i, q in enumerate(questions):
        given = answers[i] if i < len(answers) else None
        is_correct = given == q["answer_index"]
        if is_correct:
            correct_count += 1
        results.append({
            "question_id": i, "correct": is_correct, "answer_index": q["answer_index"],
            "explanation": q.get("explanation", ""), "chunk_id": q.get("chunk_id"),
        })
        if q.get("concept_id"):
            mastery.update_from_quiz(conn, q["concept_id"], is_correct)

    score = correct_count / len(questions) if questions else 0.0
    conn.execute(
        "INSERT INTO quiz_attempts (quiz_id, answers_json, score) VALUES (?,?,?)",
        (quiz_id, json.dumps(answers), score),
    )
    return {"score": score, "results": results}


@llm.fake_handler("quiz")
def _fake_quiz(system: str, user: str) -> dict:
    from ..graph.concepts import _phrases_in

    questions = []
    for m in re.finditer(r'<untrusted_source id="chunk:(\d+)">\s*(.*?)\s*</untrusted_source>', user, re.S):
        chunk_id, text = int(m.group(1)), m.group(2)
        phrases = _phrases_in(text)
        name = phrases[0] if phrases else f"chunk {chunk_id}"
        options = [name, "Something unrelated", "A common misconception", "None of the above"]
        questions.append({
            "question": f"Which of these best relates to: {text[:80].strip()}?",
            "options": options, "answer_index": 0,
            "explanation": f"Discussed in chunk {chunk_id}.", "concept": phrases[0] if phrases else None,
            "chunk_id": chunk_id,
        })
    return {"questions": questions}
