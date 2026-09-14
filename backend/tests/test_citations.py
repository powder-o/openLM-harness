"""Fast, offline unit tests for citation marker stripping and citation building."""
from __future__ import annotations

from study.agent import chat as chat_agent


def test_strip_removes_unretrieved_markers():
    text = "Entropy never decreases [c:1]. Also see [c:999999] and [f:5] and [f:7]."
    clean, kept = chat_agent.strip_and_collect_markers(text, retrieved_chunk_ids={1}, retrieved_block_ids={5})
    assert "c:999999" not in clean
    assert "[c:1]" in clean
    assert "[f:5]" in clean
    assert "[f:7]" not in clean
    assert kept == [("c", 1), ("f", 5)]


def test_strip_keeps_first_appearance_order_and_dedupes():
    text = "[c:2] then [c:1] then [c:2] again."
    clean, kept = chat_agent.strip_and_collect_markers(text, retrieved_chunk_ids={1, 2}, retrieved_block_ids=set())
    assert kept == [("c", 2), ("c", 1)]
    assert clean.count("[c:2]") == 2


def test_strip_collapses_leftover_whitespace():
    text = "Before [c:999] after."
    clean, kept = chat_agent.strip_and_collect_markers(text, retrieved_chunk_ids=set(), retrieved_block_ids=set())
    assert kept == []
    assert clean == "Before after."


def test_strip_collapses_space_before_punctuation():
    text = "See also [c:999999]."
    clean, kept = chat_agent.strip_and_collect_markers(text, retrieved_chunk_ids=set(), retrieved_block_ids=set())
    assert kept == []
    assert clean == "See also."


def test_build_citations_chunk_has_page_and_boxes(conn, sample_notebook):
    entropy_id = sample_notebook["chunks"]["entropy"]
    citations = chat_agent.build_citations(conn, [("c", entropy_id)])
    assert len(citations) == 1
    cit = citations[0]
    assert cit["marker"] == f"c:{entropy_id}"
    assert cit["kind"] == "chunk"
    assert cit["chunk_id"] == entropy_id
    assert cit["document_title"] == "Thermodynamics Basics"
    assert cit["page"] == 2
    assert cit["heading_path"] == "Thermodynamics Basics > 2. Entropy"
    assert len(cit["boxes"]) == 2
    for box in cit["boxes"]:
        assert box["page"] == 2
        assert len(box["bbox"]) == 4


def test_build_citations_figure(conn, sample_notebook):
    block_id = sample_notebook["figure_block_id"]
    citations = chat_agent.build_citations(conn, [("f", block_id)])
    assert len(citations) == 1
    cit = citations[0]
    assert cit["marker"] == f"f:{block_id}"
    assert cit["kind"] == "figure"
    assert cit["block_id"] == block_id
    assert cit["label"] == "Figure 1"
    assert cit["image_url"] == f"/api/blocks/{block_id}/image"
    assert cit["page"] == 2


def test_build_citations_skips_unknown_ids(conn, sample_notebook):
    citations = chat_agent.build_citations(conn, [("c", 999999), ("f", 999999)])
    assert citations == []


def test_end_to_end_strip_then_cite(conn, sample_notebook):
    entropy_id = sample_notebook["chunks"]["entropy"]
    text = f"Entropy increases in isolated systems [c:{entropy_id}]. Unrelated claim [c:999999]."
    clean, kept = chat_agent.strip_and_collect_markers(text, retrieved_chunk_ids={entropy_id}, retrieved_block_ids=set())
    assert "999999" not in clean
    citations = chat_agent.build_citations(conn, kept)
    assert [c["marker"] for c in citations] == [f"c:{entropy_id}"]
