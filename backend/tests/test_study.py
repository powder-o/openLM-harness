"""Tests for study tools + api/study.py (SPEC §9)."""
from __future__ import annotations

import sys
import types

import study.graph as graph_hooks
from study import db
from study.graph import merge
from study.study_tools import path


def _index(conn, sample_notebook) -> None:
    for doc_id in sample_notebook["doc_ids"]:
        graph_hooks.after_document_indexed(conn, doc_id)


def test_flashcards_generate_review_updates_due_at_and_mastery(client, conn, sample_notebook):
    nb = sample_notebook["notebook_id"]
    _index(conn, sample_notebook)

    resp = client.post("/api/study/flashcards/generate", json={
        "notebook_id": nb, "scope": {"kind": "notebook", "id": nb}, "count": 5,
    })
    assert resp.status_code == 200
    cards = resp.json()
    assert cards

    card = next((c for c in cards if c["concept_id"] is not None), None)
    assert card is not None, "expected at least one generated card to be linked to a concept"

    review_resp = client.post(f"/api/study/flashcards/{card['id']}/review", json={"grade": 1})
    assert review_resp.status_code == 200
    updated = review_resp.json()
    assert updated["reps"] == 1
    assert updated["interval_days"] == 1.0
    assert updated["due_at"] > card["due_at"]

    mastery_resp = client.get(f"/api/study/mastery", params={"notebook_id": nb})
    assert mastery_resp.status_code == 200
    mastery_map = mastery_resp.json()
    assert mastery_map[str(card["concept_id"])] > 0.0  # grade=1 -> sample 0.5, blended into prior 0


def test_flashcards_generate_returns_409_when_llm_unavailable(client, conn, sample_notebook, monkeypatch):
    nb = sample_notebook["notebook_id"]

    class _Unavailable:
        def available(self) -> bool:
            return False

    monkeypatch.setattr("study.llm.get_llm", lambda: _Unavailable())
    resp = client.post("/api/study/flashcards/generate", json={
        "notebook_id": nb, "scope": {"kind": "notebook", "id": nb}, "count": 3,
    })
    assert resp.status_code == 409
    assert resp.json()["detail"] == "DeepSeek API key is not configured"


def test_quiz_generate_and_submit_scores_correctly(client, conn, sample_notebook):
    nb = sample_notebook["notebook_id"]
    _index(conn, sample_notebook)

    resp = client.post("/api/study/quiz/generate", json={
        "notebook_id": nb, "scope": {"kind": "notebook", "id": nb}, "count": 3,
    })
    assert resp.status_code == 200
    quiz_data = resp.json()
    questions = quiz_data["questions"]
    assert questions
    for q in questions:
        assert len(q["options"]) == 4
        assert 0 <= q["answer_index"] < 4

    all_correct = [q["answer_index"] for q in questions]
    submit_resp = client.post(f"/api/study/quiz/{quiz_data['id']}/submit", json={"answers": all_correct})
    assert submit_resp.status_code == 200
    result = submit_resp.json()
    assert result["score"] == 1.0
    assert all(r["correct"] for r in result["results"])

    resp2 = client.post("/api/study/quiz/generate", json={
        "notebook_id": nb, "scope": {"kind": "notebook", "id": nb}, "count": 3,
    })
    quiz2 = resp2.json()
    all_wrong = [(q["answer_index"] + 1) % 4 for q in quiz2["questions"]]
    submit_resp2 = client.post(f"/api/study/quiz/{quiz2['id']}/submit", json={"answers": all_wrong})
    assert submit_resp2.json()["score"] == 0.0


def test_guide_generation_cites_only_context_chunks(client, conn, sample_notebook):
    nb = sample_notebook["notebook_id"]
    _index(conn, sample_notebook)

    resp = client.post("/api/study/guides", json={
        "notebook_id": nb, "scope": {"kind": "document", "id": sample_notebook["doc_ids"][0]},
    })
    assert resp.status_code == 200
    guide_doc = resp.json()
    assert guide_doc["content_md"]
    assert guide_doc["title"]

    listing = client.get("/api/study/guides", params={"notebook_id": nb})
    assert listing.status_code == 200
    assert any(g["id"] == guide_doc["id"] for g in listing.json())


def test_learning_path_orders_first_law_before_second_law(conn, sample_notebook):
    nb = sample_notebook["notebook_id"]
    _index(conn, sample_notebook)

    second_law = db.one(
        conn, "SELECT * FROM concepts WHERE notebook_id = ? AND norm_name = ?",
        (nb, merge.norm_name("Second Law of Thermodynamics")),
    )
    assert second_law is not None

    result = path.learning_path(conn, second_law["id"])
    names = [s["name"] for s in result["steps"]]
    assert "First Law of Thermodynamics" in names
    assert names.index("First Law of Thermodynamics") < names.index("Second Law of Thermodynamics")
    assert result["steps"][-1]["name"] == "Second Law of Thermodynamics"
    assert result["target"]["name"] == "Second Law of Thermodynamics"


def test_notes_endpoints_create_and_update_queued_document(client, conn, monkeypatch):
    archive_id = conn.execute("INSERT INTO archives (name) VALUES ('Notes Archive')").lastrowid
    nb = conn.execute(
        "INSERT INTO notebooks (archive_id, name) VALUES (?, 'Notes Notebook')", (archive_id,)
    ).lastrowid

    enqueued: list[int] = []
    fake_worker = types.ModuleType("study.ingest.worker")
    fake_worker.enqueue = lambda document_id: enqueued.append(document_id)
    monkeypatch.setitem(sys.modules, "study.ingest.worker", fake_worker)

    resp = client.post(f"/api/notebooks/{nb}/notes", json={"title": "My Note", "content_md": "# Hello\nBody text."})
    assert resp.status_code == 201
    doc = resp.json()
    assert doc["kind"] == "note"
    assert doc["status"] == "queued"
    assert enqueued == [doc["id"]]

    get_resp = client.get(f"/api/documents/{doc['id']}/note")
    assert get_resp.status_code == 200
    assert get_resp.json() == {"title": "My Note", "content_md": "# Hello\nBody text."}

    put_resp = client.put(f"/api/documents/{doc['id']}/note", json={"title": "Updated", "content_md": "New body"})
    assert put_resp.status_code == 200
    updated_doc = put_resp.json()
    assert updated_doc["title"] == "Updated"
    assert updated_doc["status"] == "queued"
    assert enqueued == [doc["id"], doc["id"]]

    get_resp2 = client.get(f"/api/documents/{doc['id']}/note")
    assert get_resp2.json() == {"title": "Updated", "content_md": "New body"}
