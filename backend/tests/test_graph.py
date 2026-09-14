"""Tests for the graph tiers: extraction, merging, topics, payloads, highlight (SPEC §8)."""
from __future__ import annotations

import study.graph as graph_hooks
from study import db
from study.graph import build, highlight, merge
from study.index import embed


def _index(conn, sample_notebook) -> None:
    for doc_id in sample_notebook["doc_ids"]:
        graph_hooks.after_document_indexed(conn, doc_id)


def _concept(conn, notebook_id: int, name: str) -> dict | None:
    return db.one(
        conn, "SELECT * FROM concepts WHERE notebook_id = ? AND norm_name = ?",
        (notebook_id, merge.norm_name(name)),
    )


def test_extraction_merges_entropy_across_documents_and_finds_prerequisite(conn, sample_notebook):
    nb = sample_notebook["notebook_id"]
    _index(conn, sample_notebook)

    concepts = db.rows(conn, "SELECT * FROM concepts WHERE notebook_id = ?", (nb,))
    assert concepts, "expected concepts to be extracted"

    entropy = _concept(conn, nb, "Entropy")
    assert entropy is not None

    mention_docs = {
        r["document_id"] for r in db.rows(
            conn,
            "SELECT ch.document_id FROM concept_mentions cm JOIN chunks ch ON ch.id = cm.chunk_id"
            " WHERE cm.concept_id = ?",
            (entropy["id"],),
        )
    }
    assert set(sample_notebook["doc_ids"]) <= mention_docs, "Entropy should be mentioned in both documents"

    first_law = _concept(conn, nb, "First Law of Thermodynamics")
    second_law = _concept(conn, nb, "Second Law of Thermodynamics")
    assert first_law and second_law
    edge = db.one(
        conn,
        "SELECT * FROM concept_edges WHERE notebook_id = ? AND relation = 'prerequisite_of'"
        " AND source_id = ? AND target_id = ?",
        (nb, first_law["id"], second_law["id"]),
    )
    assert edge is not None

    topics = db.rows(conn, "SELECT * FROM topics WHERE notebook_id = ?", (nb,))
    assert topics, "expected at least one topic"
    with_topic = db.rows(conn, "SELECT id FROM concepts WHERE notebook_id = ? AND topic_id IS NOT NULL", (nb,))
    assert with_topic


def test_notebook_graph_payload_has_all_tiers_and_valid_edges(conn, sample_notebook):
    nb = sample_notebook["notebook_id"]
    _index(conn, sample_notebook)

    payload = build.notebook_graph(conn, nb)
    assert payload["scope"] == {"kind": "notebook", "id": nb}

    tiers = {n["tier"] for n in payload["nodes"]}
    assert {"structure", "concept", "topic"} <= tiers

    node_ids = {n["id"] for n in payload["nodes"]}
    for edge in payload["edges"]:
        assert edge["source"] in node_ids, edge
        assert edge["target"] in node_ids, edge

    assert payload["stats"]["documents"] == 2
    assert payload["stats"]["concepts"] == len(db.rows(conn, "SELECT id FROM concepts WHERE notebook_id = ?", (nb,)))

    # cache: a second call without rebuilding returns the same built_at (served from graph_cache)
    payload2 = build.notebook_graph(conn, nb)
    assert payload2["built_at"] == payload["built_at"]


def test_rebuild_notebook_graph_reextract(conn, sample_notebook):
    nb = sample_notebook["notebook_id"]
    _index(conn, sample_notebook)
    before = db.rows(conn, "SELECT id FROM concepts WHERE notebook_id = ?", (nb,))
    assert before

    payload = build.rebuild_notebook_graph(conn, nb, reextract=True)
    after = db.rows(conn, "SELECT id FROM concepts WHERE notebook_id = ?", (nb,))
    assert after  # concepts exist again after a full re-extraction
    assert payload["stats"]["concepts"] == len(after)


def _add_entropy_document(conn, archive_id: int) -> tuple[int, int]:
    """A second notebook in the same archive, with one chunk mentioning Entropy (minimal rows)."""
    nb2 = conn.execute("INSERT INTO notebooks (archive_id, name) VALUES (?, 'Notebook 2')", (archive_id,)).lastrowid
    d3 = conn.execute(
        "INSERT INTO documents (notebook_id, title, kind, source_name, status)"
        " VALUES (?, 'Entropy Notes', 'md', 'entropy.md', 'ready')",
        (nb2,),
    ).lastrowid
    text = "Entropy is a central idea in thermodynamics and statistical mechanics."
    vec = embed.embed_texts([text])[0]
    conn.execute(
        "INSERT INTO chunks (document_id, notebook_id, seq, kind, text, heading_path, block_ids_json,"
        " token_count, embedding) VALUES (?,?,?,?,?,?,?,?,?)",
        (d3, nb2, 0, "text", text, "Entropy Notes", "[]", len(text.split()), embed.to_blob(vec)),
    )
    return nb2, d3


def test_archive_merges_entropy_concept_across_notebooks(conn, sample_notebook):
    archive_id = sample_notebook["archive_id"]
    nb1 = sample_notebook["notebook_id"]
    _index(conn, sample_notebook)

    nb2, d3 = _add_entropy_document(conn, archive_id)
    graph_hooks.after_document_indexed(conn, d3)

    payload = build.rebuild_archive_graph(conn, archive_id)
    acon_nodes = [n for n in payload["nodes"] if n["type"] == "concept" and n["id"].startswith("acon:")]
    entropy_nodes = [n for n in acon_nodes if n["label"] == "Entropy"]
    assert len(entropy_nodes) == 1, f"expected exactly one archive concept for Entropy, got {entropy_nodes}"
    entropy_node = entropy_nodes[0]

    node_by_id = {n["id"]: n for n in payload["nodes"]}
    mentioning_doc_nodes = [e["source"] for e in payload["edges"]
                            if e["type"] == "mentions" and e["target"] == entropy_node["id"]]
    mentioning_notebooks = {node_by_id[doc_node]["ref"]["notebook_id"] for doc_node in mentioning_doc_nodes}
    assert {nb1, nb2} <= mentioning_notebooks


def test_highlight_for_chunks_notebook_scope(conn, sample_notebook):
    nb = sample_notebook["notebook_id"]
    _index(conn, sample_notebook)
    # The "energy" chunk mentions only First Law of Thermodynamics (Second Law is discussed in a
    # different chunk/section), so Second Law should surface as a *related* 1-hop neighbour via the
    # prerequisite_of edge rather than as primary.
    energy_chunk = sample_notebook["chunks"]["energy"]

    result = highlight.highlight_for_chunks(conn, f"notebook:{nb}", [energy_chunk])
    first_law = _concept(conn, nb, "First Law of Thermodynamics")
    second_law = _concept(conn, nb, "Second Law of Thermodynamics")

    assert f"doc:{sample_notebook['doc_ids'][0]}" in result["primary"]
    assert f"sec:{sample_notebook['section_ids']['energy']}" in result["primary"]
    assert f"con:{first_law['id']}" in result["primary"]
    assert f"con:{second_law['id']}" not in result["primary"]
    assert f"con:{second_law['id']}" in result["related"]
