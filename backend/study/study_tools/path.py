"""Learning path (SPEC §9): prerequisite ancestors of a concept, topologically ordered, cycle-safe,
target last.
"""
from __future__ import annotations

from .. import db
from . import mastery as mastery_tools


def learning_path(conn, concept_id: int) -> dict:
    target = db.one(conn, "SELECT id, name, notebook_id FROM concepts WHERE id = ?", (concept_id,))
    if target is None:
        raise KeyError(concept_id)

    edges = db.rows(
        conn,
        "SELECT source_id, target_id FROM concept_edges WHERE notebook_id = ? AND relation = 'prerequisite_of'",
        (target["notebook_id"],),
    )
    incoming: dict[int, list[int]] = {}
    for e in edges:
        incoming.setdefault(e["target_id"], []).append(e["source_id"])

    order: list[int] = []
    visited: set[int] = set()

    def visit(node: int, in_progress: frozenset[int]) -> None:
        if node in visited or node in in_progress:
            return  # cycle-safe: skip an ancestor already on this DFS path
        in_progress = in_progress | {node}
        for prereq in incoming.get(node, []):
            visit(prereq, in_progress)
        if node not in visited:
            visited.add(node)
            if node != concept_id:
                order.append(node)

    visit(concept_id, frozenset())
    order.append(concept_id)  # target last

    mastery_map = mastery_tools.get_mastery(conn, target["notebook_id"])
    steps = []
    for cid in order:
        c = db.one(conn, "SELECT id, name, summary FROM concepts WHERE id = ?", (cid,))
        if c is None:
            continue
        steps.append({
            "concept_id": c["id"], "name": c["name"], "summary": c["summary"],
            "mastery": mastery_map.get(str(c["id"]), 0.0), "node_id": f"con:{c['id']}",
        })
    return {"target": {"id": target["id"], "name": target["name"]}, "steps": steps}
