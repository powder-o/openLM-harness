"""highlight_for_chunks (SPEC §8): map chunk ids to the graph node ids they light up.

primary = doc, section (rolled up to graph_section_depth), figure/table, and concept (or `acon` for
an archive scope) nodes touched by the given chunks. related = 1-hop concept neighbours of the
primary concepts, plus their topic nodes.
"""
from __future__ import annotations

import json

from .. import config, db
from .structure import document_structure


def _parse_scope(scope: str) -> tuple[str, int]:
    kind, _, sid = scope.partition(":")
    return kind, int(sid)


def highlight_for_chunks(conn, scope: str, chunk_ids: list[int]) -> dict:
    kind, scope_id = _parse_scope(scope)
    if not chunk_ids:
        return {"primary": [], "related": []}

    placeholders = ",".join("?" * len(chunk_ids))
    chunk_rows = db.rows(
        conn, f"SELECT id, document_id, notebook_id, section_id, block_ids_json, kind FROM chunks"
        f" WHERE id IN ({placeholders})", chunk_ids,
    )
    by_id = {c["id"]: c for c in chunk_rows}

    primary: list[str] = []
    seen: set[str] = set()

    def add_primary(node_id: str | None) -> None:
        if node_id and node_id not in seen:
            seen.add(node_id)
            primary.append(node_id)

    resolve_by_doc: dict[int, object] = {}
    concept_ids: list[int] = []

    for chunk_id in chunk_ids:  # preserve caller's order
        c = by_id.get(chunk_id)
        if c is None:
            continue
        doc_id = c["document_id"]
        add_primary(f"doc:{doc_id}")

        if doc_id not in resolve_by_doc:
            nb = db.one(conn, "SELECT settings_json FROM notebooks WHERE id = ?", (c["notebook_id"],))
            settings = config.notebook_settings(nb["settings_json"] if nb else None)
            info = document_structure(conn, doc_id, int(settings.get("graph_section_depth", 2)))
            resolve_by_doc[doc_id] = info["resolve"]
        if c["section_id"]:
            sec = resolve_by_doc[doc_id](c["section_id"])
            if sec:
                add_primary(f"sec:{sec}")

        if c["kind"] in ("figure", "table"):
            block_ids = json.loads(c["block_ids_json"] or "[]")
            if block_ids:
                b = db.one(conn, "SELECT type FROM blocks WHERE id = ?", (block_ids[0],))
                if b:
                    prefix = "fig" if b["type"] == "figure" else "tab"
                    add_primary(f"{prefix}:{block_ids[0]}")

        for m in db.rows(conn, "SELECT concept_id FROM concept_mentions WHERE chunk_id = ?", (chunk_id,)):
            if m["concept_id"] not in concept_ids:
                concept_ids.append(m["concept_id"])

    if kind == "archive":
        acon_map: dict[int, int] = {}
        if concept_ids:
            for a in db.rows(conn, "SELECT id, member_concept_ids_json FROM archive_concepts WHERE archive_id = ?",
                              (scope_id,)):
                for cid in json.loads(a["member_concept_ids_json"] or "[]"):
                    acon_map[cid] = a["id"]
        primary_acon_ids: list[int] = []
        for cid in concept_ids:
            aid = acon_map.get(cid)
            if aid is not None:
                add_primary(f"acon:{aid}")
                primary_acon_ids.append(aid)
        related = _related_archive(conn, scope_id, primary_acon_ids, seen)
    else:
        for cid in concept_ids:
            add_primary(f"con:{cid}")
        related = _related_notebook(conn, concept_ids, seen)

    return {"primary": primary, "related": related}


def _related_notebook(conn, concept_ids: list[int], primary_seen: set[str]) -> list[str]:
    related: list[str] = []
    seen: set[str] = set()
    topic_ids: list[int] = []
    for cid in concept_ids:
        for r in db.rows(
            conn,
            "SELECT target_id AS other FROM concept_edges WHERE source_id = ?"
            " UNION SELECT source_id AS other FROM concept_edges WHERE target_id = ?",
            (cid, cid),
        ):
            node = f"con:{r['other']}"
            if node not in seen and node not in primary_seen:
                seen.add(node)
                related.append(node)
        c = db.one(conn, "SELECT topic_id FROM concepts WHERE id = ?", (cid,))
        if c and c["topic_id"] and c["topic_id"] not in topic_ids:
            topic_ids.append(c["topic_id"])
    for tid in topic_ids:
        node = f"topic:{tid}"
        if node not in seen:
            seen.add(node)
            related.append(node)
    return related


def _related_archive(conn, archive_id: int, acon_ids: list[int], primary_seen: set[str]) -> list[str]:
    """1-hop archive-concept neighbours (via concept_edges rolled up to archive_concepts), plus topics."""
    related: list[str] = []
    seen: set[str] = set()
    if not acon_ids:
        return related

    concept_to_acon: dict[int, int] = {}
    aconcepts = db.rows(conn, "SELECT id, topic_id, member_concept_ids_json FROM archive_concepts"
                        " WHERE archive_id = ?", (archive_id,))
    for a in aconcepts:
        for cid in json.loads(a["member_concept_ids_json"] or "[]"):
            concept_to_acon[cid] = a["id"]

    nb_ids = [n["id"] for n in db.rows(conn, "SELECT id FROM notebooks WHERE archive_id = ?", (archive_id,))]
    neighbours: dict[int, set[int]] = {}
    if nb_ids:
        placeholders = ",".join("?" * len(nb_ids))
        for e in db.rows(
            conn, f"SELECT source_id, target_id FROM concept_edges WHERE notebook_id IN ({placeholders})", nb_ids
        ):
            sa, ta = concept_to_acon.get(e["source_id"]), concept_to_acon.get(e["target_id"])
            if sa is not None and ta is not None and sa != ta:
                neighbours.setdefault(sa, set()).add(ta)
                neighbours.setdefault(ta, set()).add(sa)

    for aid in acon_ids:
        for other in neighbours.get(aid, ()):
            node = f"acon:{other}"
            if node not in seen and node not in primary_seen:
                seen.add(node)
                related.append(node)

    topic_by_acon = {a["id"]: a["topic_id"] for a in aconcepts}
    topic_ids: list[int] = []
    for aid in acon_ids:
        tid = topic_by_acon.get(aid)
        if tid and tid not in topic_ids:
            topic_ids.append(tid)
    for tid in topic_ids:
        node = f"topic:{tid}"
        if node not in seen:
            seen.add(node)
            related.append(node)
    return related
