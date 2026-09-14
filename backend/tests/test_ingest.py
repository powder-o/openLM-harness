"""Ingest tests. Fast tests run offline; @pytest.mark.docling tests exercise the real parser."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from study.ingest import chunk, figures

# ---------------------------------------------------------------- fast: chunker


def _b(id_, type_, text, level=None, page=None, section_id=None):
    return {"id": id_, "type": type_, "text": text, "level": level, "page": page, "section_id": section_id}


def test_chunk_blocks_respects_heading_boundaries_and_max_tokens():
    settings = {"chunk_max_tokens": 20, "chunk_overlap_tokens": 100}
    blocks = [
        _b(1, "heading", "Introduction", level=1, section_id=None),
        _b(2, "paragraph", "Energy is conserved in an isolated system according to the first law.", page=1, section_id=1),
        _b(3, "heading", "Entropy", level=2, section_id=1),
        _b(4, "paragraph", "Entropy measures the number of accessible microstates of a system.", page=2, section_id=3),
    ]
    chunks = chunk.chunk_blocks("My Doc", blocks, settings)

    assert all(c["kind"] == "text" for c in chunks)
    # a level<=2 heading forces a break: the "Introduction" paragraph and the "Entropy" paragraph
    # must land in different chunks.
    intro_chunk = next(c for c in chunks if 2 in c["block_ids"])
    entropy_chunk = next(c for c in chunks if 4 in c["block_ids"])
    assert intro_chunk is not entropy_chunk
    assert intro_chunk["heading_path"] == "My Doc > Introduction"
    assert entropy_chunk["heading_path"] == "My Doc > Introduction > Entropy"
    assert entropy_chunk["page_start"] == 2 and entropy_chunk["page_end"] == 2


def test_chunk_blocks_splits_on_max_tokens_with_overlap():
    settings = {"chunk_max_tokens": 8, "chunk_overlap_tokens": 8}
    blocks = [
        _b(1, "paragraph", "one two three four five", section_id=None),
        _b(2, "paragraph", "six seven eight nine ten", section_id=None),
    ]
    chunks = chunk.chunk_blocks("Doc", blocks, settings)
    # first block alone already estimates ~6.5 tokens; adding the second would overflow max_tokens=8,
    # so it should split into (at least) two chunks, and the small first block gets carried as overlap.
    assert len(chunks) >= 2
    assert chunks[0]["block_ids"] == [1]
    assert 1 in chunks[1]["block_ids"]  # carried overlap
    assert 2 in chunks[1]["block_ids"]


def test_chunk_blocks_figure_and_table_become_own_chunks():
    settings = {"chunk_max_tokens": 350, "chunk_overlap_tokens": 40}
    blocks = [
        _b(1, "heading", "Results", level=1, section_id=None),
        {
            "id": 2, "type": "figure", "text": "caption text", "level": None, "page": 2, "section_id": 1,
            "label": "Figure 1", "caption": "Figure 1: a chart.", "description": "A chart showing growth.",
        },
        {
            "id": 3, "type": "table", "text": "| a | b |\n|---|---|\n| 1 | 2 |", "level": None, "page": 2,
            "section_id": 1, "label": "Table 1", "caption": "Table 1: values.",
        },
        _b(4, "paragraph", "Some closing remarks about the results.", page=2, section_id=1),
    ]
    chunks = chunk.chunk_blocks("Doc", blocks, settings)
    kinds = [c["kind"] for c in chunks]
    assert "figure" in kinds and "table" in kinds
    fig_chunk = next(c for c in chunks if c["kind"] == "figure")
    assert fig_chunk["block_ids"] == [2]
    assert "Figure 1" in fig_chunk["text"] and "A chart showing growth." in fig_chunk["text"]
    table_chunk = next(c for c in chunks if c["kind"] == "table")
    assert "Table 1" in table_chunk["text"] and "| 1 | 2 |" in table_chunk["text"]


# ---------------------------------------------------------------- fast: figure labels + explicit mentions


def test_parse_label():
    assert figures.parse_label("Figure 3.2: some diagram") == "Figure 3.2"
    assert figures.parse_label("Fig. 4 shows the setup") == "Figure 4"
    assert figures.parse_label("Table 1: results") == "Table 1"
    assert figures.parse_label("no label here") is None
    assert figures.parse_label(None) is None


def test_mentions_label_explicit_mention():
    label = figures.parse_label("Figure 1: Entropy increases as a gas expands.")
    assert label == "Figure 1"
    assert figures.mentions_label("As shown in Figure 1, entropy increases.", label)
    assert figures.mentions_label("See Fig. 1 for details.", label)
    assert not figures.mentions_label("Figure 2 shows something else.", label)
    assert not figures.mentions_label("unrelated text", label)


def test_link_figures_explicit_mention(conn):
    """DB-level explicit_mention link, without going through the full pipeline."""
    a = conn.execute("INSERT INTO archives (name) VALUES ('A')").lastrowid
    nb = conn.execute("INSERT INTO notebooks (archive_id, name) VALUES (?, 'NB')", (a,)).lastrowid
    d = conn.execute(
        "INSERT INTO documents (notebook_id, title, kind, source_name, status) VALUES (?, 'Doc', 'pdf', 'x.pdf', 'ready')",
        (nb,),
    ).lastrowid
    fig_id = conn.execute(
        "INSERT INTO blocks (document_id, seq, type, text, page, label, caption) VALUES (?,0,'figure','',2,'Figure 1','Figure 1: a plot.')",
        (d,),
    ).lastrowid
    mention_id = conn.execute(
        "INSERT INTO blocks (document_id, seq, type, text, page) VALUES (?,1,'paragraph','As shown in Figure 1, things grow.',2)",
        (d,),
    ).lastrowid
    other_id = conn.execute(
        "INSERT INTO blocks (document_id, seq, type, text, page) VALUES (?,2,'paragraph','Unrelated text here.',2)",
        (d,),
    ).lastrowid

    from study.index import embed

    for bid, seq, text in ((mention_id, 0, "As shown in Figure 1, things grow."), (other_id, 1, "Unrelated text here.")):
        vec = embed.embed_texts([text])[0]
        conn.execute(
            "INSERT INTO chunks (id, document_id, notebook_id, seq, kind, text, heading_path, block_ids_json,"
            " page_start, page_end, token_count, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, d, nb, seq, "text", text, "Doc", f"[{bid}]", 2, 2, len(text.split()), embed.to_blob(vec)),
        )

    figures.link_figures(conn, d)
    rows = conn.execute(
        "SELECT chunk_id, method FROM figure_links WHERE figure_block_id=? AND method='explicit_mention'", (fig_id,)
    ).fetchall()
    chunk_ids = {r[0] for r in rows}
    assert mention_id in chunk_ids
    assert other_id not in chunk_ids


# ---------------------------------------------------------------- fast: upload through the API + real worker


def test_markdown_upload_ready_and_searchable(client, data_dir):
    r = client.post("/api/archives", json={"name": "Physics"})
    assert r.status_code == 200
    archive_id = r.json()["id"]

    r = client.post(f"/api/archives/{archive_id}/notebooks", json={"name": "Notes"})
    assert r.status_code == 200
    notebook_id = r.json()["id"]

    md = (
        "# Study Notes\n\n"
        "## Entropy\n\n"
        "Entropy measures the disorder of a system and never decreases in an isolated system.\n\n"
        "- key point one\n"
        "- key point two\n"
    )
    r = client.post(
        f"/api/notebooks/{notebook_id}/documents",
        files={"files": ("notes.md", md.encode(), "text/markdown")},
    )
    assert r.status_code == 200, r.text
    doc = r.json()[0]
    doc_id = doc["id"]
    assert doc["kind"] == "md"
    assert doc["title"] == "notes"

    status = None
    for _ in range(100):
        got = client.get(f"/api/documents/{doc_id}")
        assert got.status_code == 200
        status = got.json()["status"]
        if status in ("ready", "error"):
            break
        time.sleep(0.1)
    assert status == "ready", client.get(f"/api/documents/{doc_id}").json()

    detail = client.get(f"/api/documents/{doc_id}").json()
    assert detail["block_count"] > 0
    assert detail["chunk_count"] > 0

    blocks = client.get(f"/api/documents/{doc_id}/blocks").json()
    assert any(b["type"] == "heading" for b in blocks)

    hits = client.get("/api/search", params={"scope": f"notebook:{notebook_id}", "q": "entropy disorder"}).json()
    assert len(hits) > 0
    assert any("entropy" in h["text"].lower() for h in hits)


# ---------------------------------------------------------------- slow: real docling


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "_generated"


def _fixtures():
    from tests.fixtures.make_fixtures import make_fixtures

    return make_fixtures(FIXTURES_DIR)


@pytest.mark.docling
def test_parse_docling_two_column_pdf(tmp_path):
    from study.ingest import parse_docling
    from study import config as config_mod

    paths = _fixtures()
    settings = dict(config_mod.DEFAULT_NOTEBOOK_SETTINGS)
    figures_dir = tmp_path / "figs"
    result = parse_docling.parse_file(paths["two_column.pdf"], "pdf", settings, figures_dir)

    assert result.num_pages == 2
    assert result.page_sizes and len(result.page_sizes) == 2

    paged_blocks = [b for b in result.blocks if b["page"] is not None]
    assert paged_blocks, "expected page-tagged blocks for a PDF"
    assert all(b["bbox"] is not None for b in paged_blocks if b["type"] not in ("figure", "table") or b["bbox"])

    texts = [b["text"] for b in result.blocks if b["type"] == "paragraph"]
    joined = " ".join(texts)
    left_idx = next(i for i, t in enumerate(texts) if "First Law" in t or "isolated system" in t)
    right_idx = next(i for i, t in enumerate(texts) if "Entropy is a measure" in t or "free expansion" in t)
    assert left_idx < right_idx, "left column text should precede right column text in reading order"

    fig_blocks = [b for b in result.blocks if b["type"] == "figure"]
    assert fig_blocks, "expected a figure block"
    assert any(b["image_path"] and Path(b["image_path"]).exists() for b in fig_blocks)

    table_blocks = [b for b in result.blocks if b["type"] == "table"]
    assert table_blocks, "expected a table block"
    assert any(b["table_html"] for b in table_blocks)

    # explicit-mention figure link (pure function, using the parsed label + body text)
    fig_label = next((b["label"] for b in fig_blocks if b["label"]), None)
    assert fig_label == "Figure 1"
    from study.ingest import figures as figures_mod

    assert any(figures_mod.mentions_label(t, fig_label) for t in texts)


@pytest.mark.docling
def test_parse_docling_scanned_pdf_ocr(tmp_path):
    from study.ingest import parse_docling
    from study import config as config_mod

    paths = _fixtures()
    settings = dict(config_mod.DEFAULT_NOTEBOOK_SETTINGS)
    figures_dir = tmp_path / "figs"
    result = parse_docling.parse_file(paths["scanned.pdf"], "pdf", settings, figures_dir)

    all_text = " ".join(b["text"] for b in result.blocks if b["text"])
    assert len(all_text.strip()) > 40, f"expected non-trivial OCR text, got: {all_text!r}"
