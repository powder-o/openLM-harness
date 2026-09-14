"""Hybrid search tests against the sample_notebook fixture (SPEC §7)."""
from __future__ import annotations

import pytest

from study.index import search


def test_parse_scope():
    assert search.parse_scope("notebook:1") == ("notebook", 1)
    assert search.parse_scope("archive:42") == ("archive", 42)
    with pytest.raises(ValueError):
        search.parse_scope("bogus:1")
    with pytest.raises(ValueError):
        search.parse_scope("notebook:x")


def test_scope_notebook_ids(conn, sample_notebook):
    nb_id = sample_notebook["notebook_id"]
    archive_id = sample_notebook["archive_id"]
    assert search.scope_notebook_ids(conn, "notebook", nb_id) == [nb_id]
    assert search.scope_notebook_ids(conn, "archive", archive_id) == [nb_id]


def test_hybrid_search_finds_entropy_chunks(conn, sample_notebook):
    scope = f"notebook:{sample_notebook['notebook_id']}"
    hits = search.hybrid_search(conn, scope, "entropy", k=8)
    assert len(hits) > 0
    ids = {h["chunk_id"] for h in hits}
    assert sample_notebook["chunks"]["entropy"] in ids or sample_notebook["chunks"]["boltzmann"] in ids
    for h in hits:
        assert set(h) >= {
            "chunk_id", "document_id", "document_title", "notebook_id", "kind",
            "page_start", "page_end", "heading_path", "text", "score", "figures",
        }


def test_hybrid_search_scope_filtering(conn, sample_notebook):
    nb_scope = f"notebook:{sample_notebook['notebook_id']}"
    archive_scope = f"archive:{sample_notebook['archive_id']}"

    nb_hits = search.hybrid_search(conn, nb_scope, "entropy", k=8)
    archive_hits = search.hybrid_search(conn, archive_scope, "entropy", k=8)
    assert len(archive_hits) >= len(nb_hits)

    other_archive = conn.execute("INSERT INTO archives (name) VALUES ('Other')").lastrowid
    other_nb = conn.execute(
        "INSERT INTO notebooks (archive_id, name) VALUES (?, 'Empty')", (other_archive,)
    ).lastrowid
    other_hits = search.hybrid_search(conn, f"notebook:{other_nb}", "entropy", k=8)
    assert other_hits == []


def test_hybrid_search_figures_attached(conn, sample_notebook):
    scope = f"notebook:{sample_notebook['notebook_id']}"
    hits = search.hybrid_search(conn, scope, "free expansion entropy", k=8)
    free_expansion_id = sample_notebook["chunks"]["free_expansion"]
    hit = next((h for h in hits if h["chunk_id"] == free_expansion_id), None)
    assert hit is not None, hits
    assert any(f["block_id"] == sample_notebook["figure_block_id"] for f in hit["figures"])


def test_chunk_in_scope(conn, sample_notebook):
    nb_scope = f"notebook:{sample_notebook['notebook_id']}"
    chunk_id = sample_notebook["chunks"]["entropy"]
    assert search.chunk_in_scope(conn, nb_scope, chunk_id)

    other_archive = conn.execute("INSERT INTO archives (name) VALUES ('Other')").lastrowid
    other_nb = conn.execute(
        "INSERT INTO notebooks (archive_id, name) VALUES (?, 'Empty')", (other_archive,)
    ).lastrowid
    assert not search.chunk_in_scope(conn, f"notebook:{other_nb}", chunk_id)
