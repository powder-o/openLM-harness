"""Figure/table crops, labels, vision descriptions, and figure<->text chunk links (SPEC §6, §4)."""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Callable

import numpy as np

from .. import config, db, llm
from ..index import embed

log = logging.getLogger("study.ingest.figures")

_LABEL_RE = re.compile(r"\b(figure|fig\.?|table)\s*([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE)
_MENTION_CACHE: dict[str, re.Pattern] = {}


def parse_label(caption: str | None) -> str | None:
    """Parse a "Figure 3.2" / "Fig. 4" / "Table 1" label out of a caption string."""
    if not caption:
        return None
    m = _LABEL_RE.search(caption)
    if not m:
        return None
    kind = "Table" if m.group(1).lower().startswith("table") else "Figure"
    return f"{kind} {m.group(2)}"


def mentions_label(text: str, label: str | None) -> bool:
    """True if `text` explicitly references `label` (e.g. "as shown in Figure 1")."""
    if not label or not text:
        return False
    kind, _, num = label.partition(" ")
    if not num:
        return False
    num_re = re.escape(num)
    if kind == "Table":
        pattern = rf"\btable\s*{num_re}\b"
    else:
        pattern = rf"\b(?:figure|fig\.?)\s*{num_re}\b"
    rx = _MENTION_CACHE.get(pattern)
    if rx is None:
        rx = re.compile(pattern, re.IGNORECASE)
        _MENTION_CACHE[pattern] = rx
    return rx.search(text) is not None


def _nearby_text(conn, block_row: dict, window: int = 2) -> str:
    rows_ = db.rows(
        conn,
        "SELECT text FROM blocks WHERE document_id=? AND seq BETWEEN ? AND ? AND type NOT IN ('figure','table')"
        " ORDER BY seq",
        (block_row["document_id"], block_row["seq"] - window, block_row["seq"] + window),
    )
    return " ".join(r["text"] for r in rows_ if r["text"])[:800]


def _crop_table_image(source_path: Path, block_row: dict, figures_root: Path) -> str | None:
    if not block_row.get("bbox_json") or not block_row.get("page"):
        return None
    try:
        import pymupdf as fitz

        bbox = json.loads(block_row["bbox_json"])
        with fitz.open(str(source_path)) as pdf:
            page = pdf[block_row["page"] - 1]
            w, h = page.rect.width, page.rect.height
            rect = fitz.Rect(bbox[0] * w, bbox[1] * h, bbox[2] * w, bbox[3] * h)
            pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(2, 2))
            dest = figures_root / f"{block_row['id']}.png"
            pix.save(str(dest))
        return str(dest.relative_to(config.data_dir()))
    except Exception:
        log.exception("table image crop failed for block %s", block_row["id"])
        return None


def process_figures(
    conn,
    document_id: int,
    doc_kind: str,
    source_path: Path | None,
    settings: dict,
    progress: Callable[[str], None] = lambda _d: None,
) -> None:
    """Relocate figure images (from the parser's temp path), crop table images for PDFs,
    parse labels, and generate vision descriptions (fallback: the caption)."""
    blocks = db.rows(
        conn,
        "SELECT * FROM blocks WHERE document_id=? AND type IN ('figure','table') ORDER BY seq",
        (document_id,),
    )
    if not blocks:
        return
    doc_dir = config.document_dir(document_id)
    figures_root = doc_dir / "figures"
    figures_root.mkdir(parents=True, exist_ok=True)

    llm_client = llm.get_llm()
    describe = bool(settings.get("describe_figures", True)) and llm_client.available()
    total = len(blocks)

    for i, b in enumerate(blocks, start=1):
        progress(f"{i}/{total} figures")
        label = parse_label(b.get("caption"))
        image_rel = None
        temp_path = b.get("image_path")
        if temp_path and Path(temp_path).exists():
            dest = figures_root / f"{b['id']}.png"
            try:
                shutil.move(temp_path, dest)
                image_rel = str(dest.relative_to(config.data_dir()))
            except Exception:
                log.exception("failed to relocate figure image for block %s", b["id"])
        elif b["type"] == "table" and doc_kind == "pdf" and source_path is not None:
            image_rel = _crop_table_image(source_path, b, figures_root)

        description = None
        if b["type"] == "figure":
            if describe and image_rel:
                context = " ".join(x for x in (b.get("caption"), _nearby_text(conn, b)) if x)
                try:
                    description = llm_client.describe_image(str(config.data_dir() / image_rel), context)
                except Exception:
                    log.exception("figure description failed for block %s", b["id"])
            if not description:
                description = b.get("caption") or ""

        conn.execute(
            "UPDATE blocks SET image_path=?, label=?, description=? WHERE id=?",
            (image_rel, label, description, b["id"]),
        )


def link_figures(conn, document_id: int) -> None:
    """Compute figure_links: explicit_mention (1.0), caption_adjacent (0.6), semantic (cosine >= 0.6)."""
    figs = db.rows(
        conn, "SELECT id, label, page, seq FROM blocks WHERE document_id=? AND type IN ('figure','table')",
        (document_id,),
    )
    if not figs:
        return
    chunks_ = db.rows(
        conn,
        "SELECT id, kind, text, page_start, page_end, seq, embedding, block_ids_json FROM chunks WHERE document_id=?",
        (document_id,),
    )
    text_chunks = sorted((c for c in chunks_ if c["kind"] == "text"), key=lambda c: c["seq"])
    fig_chunk_by_block: dict[int, dict] = {}
    for c in chunks_:
        if c["kind"] in ("figure", "table"):
            for bid in json.loads(c["block_ids_json"] or "[]"):
                fig_chunk_by_block[bid] = c

    links: list[tuple[int, int, str, float]] = []
    for fig in figs:
        label = fig["label"]
        if label:
            for c in text_chunks:
                if mentions_label(c["text"], label):
                    links.append((fig["id"], c["id"], "explicit_mention", 1.0))

        before = after = None
        for c in text_chunks:
            same_page = fig["page"] is None or fig["page"] in (c["page_start"], c["page_end"])
            if not same_page:
                continue
            if c["seq"] < fig["seq"]:
                before = c
            elif c["seq"] > fig["seq"] and after is None:
                after = c
        for c in (before, after):
            if c is not None:
                links.append((fig["id"], c["id"], "caption_adjacent", 0.6))

        fig_chunk = fig_chunk_by_block.get(fig["id"])
        if fig_chunk is not None and fig_chunk.get("embedding") is not None and text_chunks:
            fvec = embed.from_blob(fig_chunk["embedding"])
            scored = []
            for c in text_chunks:
                if c["embedding"] is None:
                    continue
                cvec = embed.from_blob(c["embedding"])
                if cvec is None or fvec is None or cvec.shape[0] != fvec.shape[0]:
                    continue
                score = float(np.dot(fvec, cvec))
                if score >= 0.6:
                    scored.append((score, c))
            scored.sort(key=lambda t: -t[0])
            for score, c in scored[:2]:
                links.append((fig["id"], c["id"], "semantic", score))

    with db.transaction(conn):
        conn.execute(
            "DELETE FROM figure_links WHERE figure_block_id IN (SELECT id FROM blocks WHERE document_id=?)",
            (document_id,),
        )
        for fbid, cid, method, score in links:
            conn.execute(
                "INSERT OR IGNORE INTO figure_links (figure_block_id, chunk_id, method, score) VALUES (?,?,?,?)",
                (fbid, cid, method, score),
            )
