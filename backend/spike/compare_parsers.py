"""Compare our docling-backed parser against a PyMuPDF baseline (and MinerU if installed).

Usage: uv run python spike/compare_parsers.py FILE.pdf [FILE2.pdf ...] --out spike/out
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow running as a plain script

from study import config  # noqa: E402
from study.ingest import parse_docling  # noqa: E402

_COLORS = {
    "heading": (220, 40, 40),
    "paragraph": (40, 110, 220),
    "text": (40, 110, 220),
    "list_item": (140, 90, 200),
    "caption": (230, 160, 40),
    "figure": (30, 160, 90),
    "table": (200, 40, 160),
    "equation": (90, 90, 90),
    "code": (0, 150, 150),
    "footnote": (150, 150, 0),
}


def _color_for(type_: str) -> tuple[int, int, int]:
    return _COLORS.get(type_, (100, 100, 100))


def _summarize(blocks: list[dict]) -> dict:
    by_type = Counter(b.get("type", "text") for b in blocks)
    return {
        "total": len(blocks),
        "by_type": dict(by_type),
        "figures": by_type.get("figure", 0),
        "tables": by_type.get("table", 0),
    }


def _draw_overlays(pdf_path: Path, blocks: list[dict], out_dir: Path, prefix: str) -> None:
    """One PNG per page, boxes colored by block type, numbered by reading order (blocks list order)."""
    doc = fitz.open(str(pdf_path))
    per_page: dict[int, list[tuple[int, dict]]] = {}
    for i, b in enumerate(blocks):
        page = b.get("page")
        if page is None:
            continue
        per_page.setdefault(page, []).append((i, b))

    for page_no in range(1, doc.page_count + 1):
        page = doc[page_no - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        draw = ImageDraw.Draw(img)
        for i, b in per_page.get(page_no, []):
            bbox = b.get("bbox")
            if not bbox:
                continue
            x0, y0, x1, y1 = bbox
            rect = [x0 * pix.width, y0 * pix.height, x1 * pix.width, y1 * pix.height]
            color = _color_for(b.get("type", "text"))
            draw.rectangle(rect, outline=color, width=3)
            label = f"{i}:{b.get('type', '')}"
            ty = max(0, rect[1] - 14)
            draw.rectangle([rect[0], ty, rect[0] + 8 * len(label), ty + 12], fill=(255, 255, 255))
            draw.text((rect[0] + 1, ty), label, fill=color)
        img.save(out_dir / f"{prefix}_page{page_no}.png")
    doc.close()


# ---------------------------------------------------------------- (a) our parser


def run_docling(pdf_path: Path, out_dir: Path) -> dict:
    settings = dict(config.DEFAULT_NOTEBOOK_SETTINGS)
    figures_dir = out_dir / "docling_figures"
    t0 = time.time()
    result = parse_docling.parse_file(pdf_path, "pdf", settings, figures_dir)
    elapsed = time.time() - t0

    (out_dir / "docling_blocks.json").write_text(json.dumps(result.blocks, indent=2, default=str))
    _draw_overlays(pdf_path, result.blocks, out_dir, "docling")

    summary = _summarize(result.blocks)
    summary["seconds"] = round(elapsed, 2)
    summary["ocr_used"] = settings.get("ocr", "auto") != "off"
    return summary


# ---------------------------------------------------------------- (b) PyMuPDF baseline


def run_pymupdf(pdf_path: Path, out_dir: Path) -> dict:
    t0 = time.time()
    doc = fitz.open(str(pdf_path))
    blocks: list[dict] = []
    for page_no in range(1, doc.page_count + 1):
        page = doc[page_no - 1]
        w, h = page.rect.width, page.rect.height
        raw = page.get_text("blocks")
        for b in sorted(raw, key=lambda r: (round(r[1]), r[0])):
            x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            text = text.strip()
            if not text:
                continue
            blocks.append(
                {
                    "type": "text",
                    "text": text,
                    "level": None,
                    "page": page_no,
                    "bbox": [x0 / w, y0 / h, x1 / w, y1 / h],
                    "image_path": None,
                    "table_html": None,
                    "caption": None,
                    "label": None,
                }
            )
    doc.close()
    elapsed = time.time() - t0

    (out_dir / "pymupdf_blocks.json").write_text(json.dumps(blocks, indent=2))
    _draw_overlays(pdf_path, blocks, out_dir, "pymupdf")

    summary = _summarize(blocks)
    summary["seconds"] = round(elapsed, 2)
    summary["ocr_used"] = False
    return summary


# ---------------------------------------------------------------- (c) MinerU (only if installed)


def run_mineru(pdf_path: Path, out_dir: Path) -> dict | None:
    if shutil.which("mineru") is None:
        return None
    mineru_dir = out_dir / "mineru_raw"
    mineru_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        subprocess.run(
            ["mineru", "-p", str(pdf_path), "-o", str(mineru_dir)],
            check=True,
            capture_output=True,
            timeout=600,
        )
    except Exception as exc:
        return {"error": str(exc)}
    elapsed = time.time() - t0

    content_list = next(mineru_dir.rglob("*content_list.json"), None)
    if content_list is None:
        return {"error": "content_list.json not found in MinerU output"}
    items = json.loads(content_list.read_text())

    type_map = {"text": "paragraph", "title": "heading", "image": "figure", "table": "table", "equation": "equation"}
    blocks = [
        {
            "type": type_map.get(item.get("type"), "paragraph"),
            "text": item.get("text") or item.get("table_body") or "",
            "level": item.get("text_level"),
            "page": (item.get("page_idx", 0) or 0) + 1,
            "bbox": None,
            "image_path": None,
            "table_html": item.get("table_body"),
            "caption": None,
            "label": None,
        }
        for item in items
    ]
    (out_dir / "mineru_blocks.json").write_text(json.dumps(blocks, indent=2))

    summary = _summarize(blocks)
    summary["seconds"] = round(elapsed, 2)
    summary["ocr_used"] = None
    return summary


# ---------------------------------------------------------------- driver


def _summary_row(name: str, r: dict | None) -> str:
    if r is None:
        return f"| {name} | - | - | - | - | not installed |"
    if "error" in r:
        return f"| {name} | error | - | - | - | {r['error']} |"
    by_type = ", ".join(f"{k}={v}" for k, v in sorted(r["by_type"].items()))
    return f"| {name} | {r['total']} ({by_type}) | {r['figures']} | {r['tables']} | {r['seconds']} | {r.get('ocr_used')} |"


def compare(pdf_path: Path, out_root: Path) -> dict:
    out_dir = out_root / pdf_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict | None] = {}
    results["docling (ours)"] = run_docling(pdf_path, out_dir)
    results["pymupdf baseline"] = run_pymupdf(pdf_path, out_dir)
    results["mineru"] = run_mineru(pdf_path, out_dir)

    lines = [
        f"# Parser comparison: {pdf_path.name}",
        "",
        "| parser | blocks (by type) | figures | tables | seconds | ocr |",
        "|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        lines.append(_summary_row(name, r))
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare docling / PyMuPDF / MinerU parsing on PDFs.")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("spike/out"))
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for f in args.files:
        print(f"\n=== {f} ===")
        compare(f, args.out)


if __name__ == "__main__":
    main()
