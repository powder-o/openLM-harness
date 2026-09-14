"""Shared fixtures. Tests run offline: fake LLM + fake embeddings, fresh data dir per test."""
from __future__ import annotations

import json
import os

os.environ.setdefault("STUDY_LLM_FAKE", "1")
os.environ.setdefault("STUDY_EMBED_FAKE", "1")

import pytest  # noqa: E402

# smoke_api.py is a standalone script (real uvicorn subprocess, real docling + real embeddings,
# no pytest fixtures) meant to be run directly with `uv run python tests/smoke_api.py`, not
# collected as a test module. Its name already doesn't match pytest's default `test_*.py` /
# `*_test.py` discovery patterns; this is belt-and-suspenders.
collect_ignore = ["smoke_api.py"]


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("STUDY_DATA_DIR", str(d))
    return d


@pytest.fixture
def conn(data_dir):
    from study import db

    c = db.connect()
    yield c
    c.close()


@pytest.fixture
def client(data_dir):
    from fastapi.testclient import TestClient

    from study.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _insert_block(conn, document_id, seq, type_, text="", **kw):
    cols = ["document_id", "seq", "type", "text"] + list(kw)
    vals = [document_id, seq, type_, text] + [json.dumps(v) if k == "bbox_json" else v for k, v in kw.items()]
    cur = conn.execute(
        f"INSERT INTO blocks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals
    )
    return cur.lastrowid


def _insert_chunk(conn, document_id, notebook_id, seq, text, heading_path, block_ids, section_id, page, kind="text"):
    from study.index import embed

    vec = embed.embed_texts([heading_path + "\n" + text])[0]
    cur = conn.execute(
        "INSERT INTO chunks (document_id, notebook_id, seq, kind, text, heading_path, block_ids_json, section_id,"
        " page_start, page_end, token_count, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (document_id, notebook_id, seq, kind, text, heading_path, json.dumps(block_ids), section_id,
         page, page, len(text.split()), embed.to_blob(vec)),
    )
    return cur.lastrowid


@pytest.fixture
def sample_notebook(conn):
    """Archive 'Physics' > notebook 'Thermodynamics' with two ready documents, chunks, and one figure link.

    Doc 1 is a paged PDF-like doc (pages + bboxes), doc 2 is markdown (no pages).
    Returns ids: archive_id, notebook_id, doc_ids, chunks (name -> id), figure_block_id.
    """
    a = conn.execute("INSERT INTO archives (name) VALUES ('Physics')").lastrowid
    nb = conn.execute("INSERT INTO notebooks (archive_id, name) VALUES (?, 'Thermodynamics')", (a,)).lastrowid
    d1 = conn.execute(
        "INSERT INTO documents (notebook_id, title, kind, source_name, num_pages, page_sizes_json, status)"
        " VALUES (?, 'Thermodynamics Basics', 'pdf', 'thermo.pdf', 2, '[[612,792],[612,792]]', 'ready')",
        (nb,),
    ).lastrowid
    d2 = conn.execute(
        "INSERT INTO documents (notebook_id, title, kind, source_name, status)"
        " VALUES (?, 'Statistical Mechanics Notes', 'md', 'statmech.md', 'ready')",
        (nb,),
    ).lastrowid
    chunks: dict[str, int] = {}

    h1 = _insert_block(conn, d1, 0, "heading", "Thermodynamics Basics", level=1, page=1, bbox_json=[0.1, 0.05, 0.9, 0.1])
    h_energy = _insert_block(conn, d1, 1, "heading", "1. Energy and Work", level=2, page=1, bbox_json=[0.1, 0.12, 0.6, 0.16], section_id=h1)
    p_energy = _insert_block(
        conn, d1, 2, "paragraph",
        "Energy is the capacity to do work. The First Law of Thermodynamics states that energy is conserved: "
        "the change in internal energy equals heat added minus work done by the system.",
        page=1, bbox_json=[0.1, 0.18, 0.9, 0.3], section_id=h_energy,
    )
    h_entropy = _insert_block(conn, d1, 3, "heading", "2. Entropy", level=2, page=2, bbox_json=[0.1, 0.05, 0.5, 0.09], section_id=h1)
    p_entropy = _insert_block(
        conn, d1, 4, "paragraph",
        "Entropy measures the number of microscopic configurations of a system. The Second Law of "
        "Thermodynamics states that the entropy of an isolated system never decreases. Understanding the "
        "First Law of Thermodynamics is a prerequisite for the Second Law.",
        page=2, bbox_json=[0.1, 0.1, 0.9, 0.25], section_id=h_entropy,
    )
    fig = _insert_block(
        conn, d1, 5, "figure", "Figure 1: Entropy increases as a gas expands into a vacuum.",
        page=2, bbox_json=[0.2, 0.3, 0.8, 0.6], section_id=h_entropy, label="Figure 1",
        caption="Figure 1: Entropy increases as a gas expands into a vacuum.",
        description="A diagram of a box where gas on the left expands into the empty right half.",
    )
    p_fig = _insert_block(
        conn, d1, 6, "paragraph",
        "As shown in Figure 1, free expansion of a gas increases entropy even though no heat is exchanged.",
        page=2, bbox_json=[0.1, 0.62, 0.9, 0.7], section_id=h_entropy,
    )
    chunks["energy"] = _insert_chunk(conn, d1, nb, 0, conn.execute("SELECT text FROM blocks WHERE id=?", (p_energy,)).fetchone()[0],
                                     "Thermodynamics Basics > 1. Energy and Work", [h_energy, p_energy], h_energy, 1)
    chunks["entropy"] = _insert_chunk(conn, d1, nb, 1, conn.execute("SELECT text FROM blocks WHERE id=?", (p_entropy,)).fetchone()[0],
                                      "Thermodynamics Basics > 2. Entropy", [h_entropy, p_entropy], h_entropy, 2)
    chunks["figure1"] = _insert_chunk(conn, d1, nb, 2,
                                      "Figure 1: Entropy increases as a gas expands into a vacuum. A diagram of a box where gas on the left expands into the empty right half.",
                                      "Thermodynamics Basics > 2. Entropy", [fig], h_entropy, 2, kind="figure")
    chunks["free_expansion"] = _insert_chunk(conn, d1, nb, 3, conn.execute("SELECT text FROM blocks WHERE id=?", (p_fig,)).fetchone()[0],
                                             "Thermodynamics Basics > 2. Entropy", [p_fig], h_entropy, 2)
    conn.execute(
        "INSERT INTO figure_links (figure_block_id, chunk_id, method, score) VALUES (?, ?, 'explicit_mention', 1.0)",
        (fig, chunks["free_expansion"]),
    )

    g1 = _insert_block(conn, d2, 0, "heading", "Statistical Mechanics Notes", level=1)
    g_boltz = _insert_block(conn, d2, 1, "heading", "Boltzmann Entropy", level=2, section_id=g1)
    q_boltz = _insert_block(
        conn, d2, 2, "paragraph",
        "Boltzmann related Entropy to microstates with S = k log W. This statistical view of entropy explains "
        "why the Second Law of Thermodynamics holds.",
        section_id=g_boltz,
    )
    g_carnot = _insert_block(conn, d2, 3, "heading", "Heat Engines", level=2, section_id=g1)
    q_carnot = _insert_block(
        conn, d2, 4, "paragraph",
        "A Carnot engine is an idealized heat engine whose efficiency depends only on the reservoir "
        "temperatures. Carnot efficiency follows from the Second Law of Thermodynamics.",
        section_id=g_carnot,
    )
    chunks["boltzmann"] = _insert_chunk(conn, d2, nb, 0, conn.execute("SELECT text FROM blocks WHERE id=?", (q_boltz,)).fetchone()[0],
                                        "Statistical Mechanics Notes > Boltzmann Entropy", [g_boltz, q_boltz], g_boltz, None)
    chunks["carnot"] = _insert_chunk(conn, d2, nb, 1, conn.execute("SELECT text FROM blocks WHERE id=?", (q_carnot,)).fetchone()[0],
                                     "Statistical Mechanics Notes > Heat Engines", [g_carnot, q_carnot], g_carnot, None)
    return {
        "archive_id": a,
        "notebook_id": nb,
        "doc_ids": [d1, d2],
        "chunks": chunks,
        "figure_block_id": fig,
        "section_ids": {"energy": h_energy, "entropy": h_entropy, "boltzmann": g_boltz, "carnot": g_carnot},
    }
