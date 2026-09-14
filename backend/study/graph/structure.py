"""Structure tier (SPEC §8): deterministic, no LLM involved.

document -> sections (headings with level <= graph_section_depth; deeper headings roll up to their
nearest qualifying ancestor) -> figures/tables.
"""
from __future__ import annotations

from .. import db


def document_structure(conn, document_id: int, max_depth: int) -> dict:
    """Returns:
      sections: [{id, text, level, page}] - the qualifying (<=max_depth) heading blocks
      section_parents: {heading_block_id: parent_heading_block_id_or_None} for qualifying sections
      figures / tables: [{id, label, caption, page, section_id, effective_section_id}]
      resolve: block_id -> nearest qualifying ancestor heading id (or None); callable, reusable
                for mapping a chunk's `section_id` to its graph section node.
    """
    headings = db.rows(
        conn, "SELECT id, text, level, page, section_id FROM blocks WHERE document_id = ? AND type = 'heading'"
        " ORDER BY seq", (document_id,),
    )
    by_id = {h["id"]: h for h in headings}
    resolved: dict[int, int | None] = {}

    def resolve(hid: int | None) -> int | None:
        if hid is None:
            return None
        if hid in resolved:
            return resolved[hid]
        h = by_id.get(hid)
        if h is None:
            resolved[hid] = None
            return None
        if (h["level"] or 1) <= max_depth:
            resolved[hid] = hid
        else:
            resolved[hid] = resolve(h["section_id"])
        return resolved[hid]

    for hid in list(by_id):
        resolve(hid)

    sections = []
    parents: dict[int, int | None] = {}
    for hid, h in by_id.items():
        if resolved.get(hid) == hid:
            sections.append(h)
            parents[hid] = resolve(h["section_id"])

    figures = db.rows(
        conn, "SELECT id, label, caption, page, section_id FROM blocks WHERE document_id = ? AND type = 'figure'",
        (document_id,),
    )
    tables = db.rows(
        conn, "SELECT id, label, caption, page, section_id FROM blocks WHERE document_id = ? AND type = 'table'",
        (document_id,),
    )
    for row in figures + tables:
        row["effective_section_id"] = resolve(row["section_id"])

    return {
        "sections": sections, "section_parents": parents, "figures": figures, "tables": tables,
        "resolve": resolve,
    }
