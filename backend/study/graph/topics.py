"""Topic detection (SPEC §8): cluster the concept graph (relations + co-mentions), label each
community with the LLM (falling back to the highest-degree concept's name when unavailable)."""
from __future__ import annotations

import json
import re

import networkx as nx

from .. import db, llm
from .cluster_graphify import cluster

_TOP_N_FOR_LABEL = 8

_LABEL_SYSTEM = f"""\
You label a cluster of related study concepts with a short topic name.
Output ONLY valid JSON: {{"label": "2-5 word topic name", "summary": "one sentence describing it"}}.
{llm.UNTRUSTED_RULE}
"""


def _concept_graph(conn, notebook_id: int) -> nx.Graph:
    G = nx.Graph()
    for c in db.rows(conn, "SELECT id, name FROM concepts WHERE notebook_id = ?", (notebook_id,)):
        G.add_node(c["id"], label=c["name"])
    for e in db.rows(conn, "SELECT source_id, target_id FROM concept_edges WHERE notebook_id = ?", (notebook_id,)):
        _add_weight(G, e["source_id"], e["target_id"], 1.0)

    by_chunk: dict[int, list[int]] = {}
    for m in db.rows(
        conn,
        "SELECT cm.concept_id, cm.chunk_id FROM concept_mentions cm JOIN concepts c ON c.id = cm.concept_id"
        " WHERE c.notebook_id = ?",
        (notebook_id,),
    ):
        by_chunk.setdefault(m["chunk_id"], []).append(m["concept_id"])
    for concept_ids in by_chunk.values():
        uniq = sorted(set(concept_ids))
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                _add_weight(G, uniq[i], uniq[j], 0.5)
    return G


def _archive_concept_graph(conn, archive_id: int) -> tuple[nx.Graph, dict[int, int]]:
    G = nx.Graph()
    concept_to_acon: dict[int, int] = {}
    for a in db.rows(conn, "SELECT id, name, member_concept_ids_json FROM archive_concepts WHERE archive_id = ?",
                      (archive_id,)):
        G.add_node(a["id"], label=a["name"])
        for cid in json.loads(a["member_concept_ids_json"] or "[]"):
            concept_to_acon[cid] = a["id"]

    nb_ids = [n["id"] for n in db.rows(conn, "SELECT id FROM notebooks WHERE archive_id = ?", (archive_id,))]
    if nb_ids:
        placeholders = ",".join("?" * len(nb_ids))
        for e in db.rows(
            conn, f"SELECT source_id, target_id FROM concept_edges WHERE notebook_id IN ({placeholders})", nb_ids
        ):
            a, b = concept_to_acon.get(e["source_id"]), concept_to_acon.get(e["target_id"])
            if a is not None and b is not None and a != b:
                _add_weight(G, a, b, 1.0)
    return G, concept_to_acon


def _add_weight(G: nx.Graph, a, b, w: float) -> None:
    if G.has_edge(a, b):
        G[a][b]["weight"] += w
    else:
        G.add_edge(a, b, weight=w)


def _label(llm_client, top_names: list[str]) -> tuple[str, str]:
    if not top_names:
        return "Topic", ""
    if not llm_client.available():
        return top_names[0], ""
    try:
        out = llm_client.chat_json(
            task="topic_label", system=_LABEL_SYSTEM,
            user=f"Top concepts by importance: {', '.join(top_names)}",
        )
        label = str(out.get("label") or top_names[0]).strip() or top_names[0]
        return label, str(out.get("summary") or "")
    except Exception:
        return top_names[0], ""


def _store_topics(conn, G: nx.Graph, communities: dict[int, list], *, notebook_id=None, archive_id=None,
                   member_table: str) -> None:
    llm_client = llm.get_llm()
    names = {n: G.nodes[n].get("label", str(n)) for n in G.nodes}
    for members in communities.values():
        if not members:
            continue
        top = sorted(members, key=lambda n: (-G.degree(n), n))[:_TOP_N_FOR_LABEL]
        label, summary = _label(llm_client, [names[n] for n in top])
        tid = conn.execute(
            "INSERT INTO topics (notebook_id, archive_id, label, summary, member_ids_json) VALUES (?,?,?,?,?)",
            (notebook_id, archive_id, label, summary, json.dumps(members)),
        ).lastrowid
        placeholders = ",".join("?" * len(members))
        conn.execute(f"UPDATE {member_table} SET topic_id = ? WHERE id IN ({placeholders})", [tid, *members])


def recompute_topics(conn, notebook_id: int) -> None:
    G = _concept_graph(conn, notebook_id)
    conn.execute("DELETE FROM topics WHERE notebook_id = ?", (notebook_id,))
    conn.execute("UPDATE concepts SET topic_id = NULL WHERE notebook_id = ?", (notebook_id,))
    if G.number_of_nodes() == 0:
        return
    communities = cluster(G)
    _store_topics(conn, G, communities, notebook_id=notebook_id, member_table="concepts")


def recompute_archive_topics(conn, archive_id: int) -> None:
    G, _ = _archive_concept_graph(conn, archive_id)
    conn.execute("DELETE FROM topics WHERE archive_id = ?", (archive_id,))
    conn.execute("UPDATE archive_concepts SET topic_id = NULL WHERE archive_id = ?", (archive_id,))
    if G.number_of_nodes() == 0:
        return
    communities = cluster(G)
    _store_topics(conn, G, communities, archive_id=archive_id, member_table="archive_concepts")


@llm.fake_handler("topic_label")
def _fake_topic_label(system: str, user: str) -> dict:
    m = re.search(r"Top concepts by importance: (.+)", user)
    names = [n.strip() for n in m.group(1).split(",")] if m else []
    top = names[0] if names and names[0] else "Topic"
    return {"label": top, "summary": f"Concepts related to {top}."}
