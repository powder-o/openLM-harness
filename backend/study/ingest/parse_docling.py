"""Docling-backed parser: pdf/docx/html/web -> canonical blocks (SPEC §6, §1, §4).

Parsers must be usable without the DB; the spike (spike/compare_parsers.py) calls parse_file directly.
Section ids are NOT assigned here (the pipeline assigns them at insert time from block order + type).
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    HeadingHierarchyOptions,
    OcrAutoOptions,
    OcrMacOptions,
    OcrMode,
    PdfPipelineOptions,
    TableStructureOptions,
)
from docling.document_converter import DocumentConverter, HTMLFormatOption, PdfFormatOption, WordFormatOption
from docling_core.types.doc.labels import DocItemLabel

from .figures import parse_label

log = logging.getLogger("study.ingest.parse_docling")

_HTML_KINDS = {"html", "web"}
_SKIP_LABELS = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}
_HEADING_LABELS = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
_PARAGRAPH_LABELS = {DocItemLabel.TEXT, DocItemLabel.PARAGRAPH, DocItemLabel.REFERENCE}
_PICTURE_LABELS = {DocItemLabel.PICTURE, DocItemLabel.CHART}
_TABLE_LABELS = {DocItemLabel.TABLE, DocItemLabel.DOCUMENT_INDEX}

_MAC_LOCALE = {"en": "en-US", "fr": "fr-FR", "de": "de-DE", "es": "es-ES"}


@dataclass
class ParseResult:
    title: str
    num_pages: int | None
    page_sizes: list[list[float]] | None
    blocks: list[dict[str, Any]]


_converters: dict[tuple, DocumentConverter] = {}


def _ocr_options(mode: OcrMode, lang: list[str]):
    if sys.platform == "darwin":
        try:
            import ocrmac  # noqa: F401

            kwargs: dict[str, Any] = {"mode": mode}
            if lang:
                kwargs["lang"] = [_MAC_LOCALE.get(code, code) for code in lang]
            return OcrMacOptions(**kwargs)
        except ImportError:
            pass
    return OcrAutoOptions(mode=mode, lang=list(lang or []))


def _pdf_pipeline_options(settings: dict) -> PdfPipelineOptions:
    ocr = settings.get("ocr", "auto")
    if ocr == "off":
        do_ocr, mode = False, OcrMode.DEFAULT
    elif ocr == "force":
        do_ocr, mode = True, OcrMode.FULL_PAGE
    else:
        do_ocr, mode = True, OcrMode.DEFAULT

    opts = PdfPipelineOptions()
    opts.do_ocr = do_ocr
    opts.ocr_options = _ocr_options(mode, list(settings.get("ocr_lang") or []))
    opts.do_table_structure = bool(settings.get("table_structure", True))
    opts.table_structure_options = TableStructureOptions()
    opts.generate_picture_images = True
    opts.images_scale = 2.0
    opts.heading_hierarchy_options = HeadingHierarchyOptions(enabled=True)
    return opts


def _converter_for(docling_kind: str, settings: dict) -> DocumentConverter:
    key = (
        docling_kind,
        settings.get("ocr", "auto"),
        bool(settings.get("table_structure", True)),
        tuple(settings.get("ocr_lang") or []),
    )
    conv = _converters.get(key)
    if conv is not None:
        return conv

    format_options: dict[Any, Any] = {}
    if docling_kind == "pdf":
        format_options[InputFormat.PDF] = PdfFormatOption(pipeline_options=_pdf_pipeline_options(settings))
    elif docling_kind == "docx":
        format_options[InputFormat.DOCX] = WordFormatOption()
    elif docling_kind == "html":
        format_options[InputFormat.HTML] = HTMLFormatOption()
    else:
        raise ValueError(f"unsupported docling kind: {docling_kind!r}")

    conv = DocumentConverter(allowed_formats=list(format_options), format_options=format_options)
    _converters[key] = conv
    return conv


def _resolve_caption_refs(doc) -> set[str]:
    refs: set[str] = set()
    for coll in (doc.pictures, doc.tables):
        for item in coll:
            for c in getattr(item, "captions", []):
                refs.add(c.cref)
    return refs


def _doc_title(doc) -> str | None:
    for item, _lvl in doc.iterate_items(with_groups=False):
        if getattr(item, "label", None) == DocItemLabel.TITLE:
            text = item.text.strip()
            if text:
                return text
    for item, _lvl in doc.iterate_items(with_groups=False):
        if getattr(item, "label", None) == DocItemLabel.SECTION_HEADER and getattr(item, "level", None) == 1:
            text = item.text.strip()
            if text:
                return text
    return None


def _page_bbox(doc, item) -> tuple[int | None, list[float] | None]:
    if not item.prov:
        return None, None
    prov = item.prov[0]
    page = prov.page_no
    page_item = doc.pages.get(page)
    if page_item is None or prov.bbox is None:
        return page, None
    size = page_item.size
    if not size.width or not size.height:
        return page, None
    bbox_tl = prov.bbox.to_top_left_origin(page_height=size.height)
    norm = bbox_tl.normalized(page_size=size)
    return page, [norm.l, norm.t, norm.r, norm.b]


def _new_block(
    type_: str,
    text: str = "",
    level: int | None = None,
    page: int | None = None,
    bbox: list[float] | None = None,
    image_path: str | None = None,
    table_html: str | None = None,
    caption: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    return {
        "type": type_,
        "text": text,
        "level": level,
        "page": page,
        "bbox": bbox,
        "image_path": image_path,
        "table_html": table_html,
        "caption": caption,
        "label": label,
    }


def _convert_item(doc, item, idx: int, figures_dir: Path) -> dict[str, Any] | None:
    label = getattr(item, "label", None)
    if label is None:
        return None
    page, bbox = _page_bbox(doc, item)

    if label in _HEADING_LABELS:
        text = item.text.strip()
        level = 1 if label == DocItemLabel.TITLE else int(getattr(item, "level", 1) or 1)
        return _new_block("heading", text=text, level=level, page=page, bbox=bbox)

    if label == DocItemLabel.LIST_ITEM:
        return _new_block("list_item", text=item.text.strip(), page=page, bbox=bbox)

    if label == DocItemLabel.FOOTNOTE:
        return _new_block("footnote", text=item.text.strip(), page=page, bbox=bbox)

    if label == DocItemLabel.FORMULA:
        return _new_block("equation", text=item.text.strip(), page=page, bbox=bbox)

    if label == DocItemLabel.CODE:
        return _new_block("code", text=item.text.strip(), page=page, bbox=bbox)

    if label == DocItemLabel.CAPTION:
        return _new_block("caption", text=item.text.strip(), page=page, bbox=bbox)

    if label in _PICTURE_LABELS:
        caption = item.caption_text(doc).strip()
        image_path = None
        try:
            pil = item.get_image(doc)
        except Exception:
            pil = None
        if pil is not None:
            dest = figures_dir / f"{idx}.png"
            try:
                pil.save(dest)
                image_path = str(dest)
            except Exception:
                log.warning("failed to save picture image for %s", getattr(item, "self_ref", "?"), exc_info=True)
        return _new_block(
            "figure",
            text=caption,
            page=page,
            bbox=bbox,
            image_path=image_path,
            caption=caption or None,
            label=parse_label(caption) if caption else None,
        )

    if label in _TABLE_LABELS:
        caption = item.caption_text(doc).strip()
        try:
            html = item.export_to_html(doc)
        except Exception:
            log.warning("table export_to_html failed for %s", getattr(item, "self_ref", "?"), exc_info=True)
            html = ""
        try:
            text = item.export_to_markdown(doc)
        except Exception:
            text = ""
        return _new_block(
            "table",
            text=text,
            page=page,
            bbox=bbox,
            table_html=html,
            caption=caption or None,
            label=parse_label(caption) if caption else None,
        )

    if label in _PARAGRAPH_LABELS:
        return _new_block("paragraph", text=item.text.strip(), page=page, bbox=bbox)

    return None


def parse_file(path: str | Path, kind: str, settings: dict, figures_dir: str | Path) -> ParseResult:
    """kind: 'pdf' | 'docx' | 'html' | 'web'. figures_dir: temp dir for picture crops (absolute
    paths in the result; the pipeline relocates/relativizes them)."""
    path = Path(path)
    figures_dir = Path(figures_dir)
    figures_dir.mkdir(parents=True, exist_ok=True)

    docling_kind = "html" if kind == "web" else kind
    converter = _converter_for(docling_kind, settings)
    result = converter.convert(path)
    doc = result.document

    page_sizes: list[list[float]] | None = None
    num_pages: int | None = None
    if doc.pages:
        ordered = sorted(doc.pages)
        num_pages = len(ordered)
        page_sizes = [[doc.pages[p].size.width, doc.pages[p].size.height] for p in ordered]

    title = _doc_title(doc) or path.stem

    skip_refs = _resolve_caption_refs(doc)
    blocks: list[dict[str, Any]] = []
    for item, _lvl in doc.iterate_items(with_groups=False):
        label = getattr(item, "label", None)
        if label is None or label in _SKIP_LABELS:
            continue
        if getattr(item, "self_ref", None) in skip_refs:
            continue
        block = _convert_item(doc, item, len(blocks), figures_dir)
        if block is None:
            continue
        if not block["text"] and block["type"] not in ("figure", "table"):
            continue
        blocks.append(block)

    if kind != "pdf":
        for b in blocks:
            b["page"] = None
            b["bbox"] = None

    return ParseResult(title=title, num_pages=num_pages, page_sizes=page_sizes, blocks=blocks)
