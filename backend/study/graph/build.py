"""Builds and caches the notebook/archive graph payloads (SPEC §8)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from .. import config, db
from . import layout, merge, structure, topics
from .concepts import extract_for_document


# ---------------------------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------------------------

def _cache_get(conn, key: str) -> dict | None:
    row = db.one(conn, "SELECT payload_json, dirty FROM graph_cache WHERE key = ?", (key,))
    if row and not row["dirty"] and row["payload_json"]:
        return json.loads(row["payload_json"])
    return None


def _cache_put(conn, key: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO graph_cache (key, payload_json, built_at, dirty) VALUES (?, ?, ?, 0)"
        " ON CONFLICT(key) DO UPDATE SET payload_json = excluded.payload_json,"
        " built_at = excluded.built_at, dirty = 0",
        (key, json.dumps(payload), payload["built_at"]),
    )


# ---------------------------------------------------------------------------------------------
# Extraction orchestration (shared by the ingest hook and the reextract-rebuild path)
# ---------------------------------------------------------------------------------------------

def extract_and_merge_document(conn, document_id: int, notebook_id: int, progress=lambda _d: None) -> None:
    result = extract_for_document(conn, document_id, progress)
    merge.apply_extraction(conn, notebook_id, result)


def _reextract_notebook(conn, notebook_id: int) -> None:
    docs = db.rows(conn, "SELECT id FROM documents WHERE notebook_id = ? AND status != 'error'", (notebook_id,))
    conn.execute("DELETE FROM concept_edges WHERE notebook_id = ?", (notebook_id,))
    conn.execute(
        "DELETE FROM concept_mentions WHERE concept_id IN (SELECT id FROM concepts WHERE notebook_id = ?)",
        (notebook_id,),
    )
    conn.execute("DELETE FROM concepts WHERE notebook_id = ?", (notebook_id,))
    for d in docs:
        extract_and_merge_document(conn, d["id"], notebook_id)


# ---------------------------------------------------------------------------------------------
# Notebook payload
# ---------------------------------------------------------------------------------------------

def _build_notebook_payload(conn, notebook_id: int) -> dict:
    nb = db.one(conn, "SELECT * FROM notebooks WHERE id = ?", (notebook_id,))
    if nb is None:
        raise ValueError(f"notebook {notebook_id} not found")
    settings = config.notebook_settings(nb["settings_json"])
    max_depth = int(settings.get("graph_section_depth", 2))
    documents = db.rows(conn, "SELECT id, title FROM documents WHERE notebook_id = ? ORDER BY id", (notebook_id,))

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_seq = [0]

    def add_edge(source: str, target: str, type_: str, **kw) -> None:
        edge_seq[0] += 1
        e = {"id": f"e{edge_seq[0]}", "source": source, "target": target, "type": type_}
        e.update(kw)
        edges.append(e)

    nb_node = f"nb:{notebook_id}"
    nodes[nb_node] = {"id": nb_node, "type": "notebook", "label": nb["name"], "tier": "structure",
                       "ref": {"notebook_id": notebook_id}}

    section_effective_of_chunk: dict[int, int | None] = {}

    for doc in documents:
        doc_id = doc["id"]
        doc_node = f"doc:{doc_id}"
        nodes[doc_node] = {"id": doc_node, "type": "document", "label": doc["title"], "tier": "structure",
                            "ref": {"document_id": doc_id, "notebook_id": notebook_id}}
        add_edge(nb_node, doc_node, "contains")

        info = structure.document_structure(conn, doc_id, max_depth)
        for h in info["sections"]:
            sec_node = f"sec:{h['id']}"
            nodes[sec_node] = {
                "id": sec_node, "type": "section", "label": h["text"], "tier": "structure",
                "ref": {"document_id": doc_id, "block_id": h["id"], "notebook_id": notebook_id, "page": h.get("page")},
            }
        for h in info["sections"]:
            parent = info["section_parents"].get(h["id"])
            parent_node = f"sec:{parent}" if parent else doc_node
            add_edge(parent_node, f"sec:{h['id']}", "contains")

        for f in info["figures"]:
            fig_node = f"fig:{f['id']}"
            nodes[fig_node] = {
                "id": fig_node, "type": "figure", "label": f.get("label") or f.get("caption") or "Figure",
                "tier": "structure",
                "ref": {"document_id": doc_id, "block_id": f["id"], "notebook_id": notebook_id, "page": f.get("page")},
            }
            parent_node = f"sec:{f['effective_section_id']}" if f["effective_section_id"] else doc_node
            add_edge(parent_node, fig_node, "contains")
        for t in info["tables"]:
            tab_node = f"tab:{t['id']}"
            nodes[tab_node] = {
                "id": tab_node, "type": "table", "label": t.get("label") or t.get("caption") or "Table",
                "tier": "structure",
                "ref": {"document_id": doc_id, "block_id": t["id"], "notebook_id": notebook_id, "page": t.get("page")},
            }
            parent_node = f"sec:{t['effective_section_id']}" if t["effective_section_id"] else doc_node
            add_edge(parent_node, tab_node, "contains")

        resolve = info["resolve"]
        for ch in db.rows(conn, "SELECT id, section_id FROM chunks WHERE document_id = ?", (doc_id,)):
            section_effective_of_chunk[ch["id"]] = resolve(ch["section_id"]) if ch["section_id"] else None

    # ---- concept tier ----
    concepts = db.rows(conn, "SELECT * FROM concepts WHERE notebook_id = ?", (notebook_id,))
    mastery_map = {
        m["concept_id"]: m["score"] for m in db.rows(
            conn,
            "SELECT concept_id, score FROM concept_mastery WHERE concept_id IN"
            " (SELECT id FROM concepts WHERE notebook_id = ?)",
            (notebook_id,),
        )
    }
    for c in concepts:
        con_node = f"con:{c['id']}"
        mention = db.one(
            conn,
            "SELECT ch.document_id, ch.block_ids_json, ch.page_start FROM concept_mentions cm"
            " JOIN chunks ch ON ch.id = cm.chunk_id WHERE cm.concept_id = ? LIMIT 1",
            (c["id"],),
        )
        ref = {"concept_id": c["id"], "notebook_id": notebook_id}
        if mention:
            ref["document_id"] = mention["document_id"]
            ref["page"] = mention["page_start"]
            block_ids = json.loads(mention["block_ids_json"] or "[]")
            if block_ids:
                ref["block_id"] = block_ids[0]
        nodes[con_node] = {
            "id": con_node, "type": "concept", "label": c["name"], "tier": "concept", "kind": c["kind"],
            "topic": f"topic:{c['topic_id']}" if c["topic_id"] else None,
            "mastery": mastery_map.get(c["id"], 0.0), "ref": ref,
        }

    mention_rows = db.rows(
        conn,
        "SELECT cm.concept_id, cm.chunk_id FROM concept_mentions cm JOIN concepts c ON c.id = cm.concept_id"
        " WHERE c.notebook_id = ?",
        (notebook_id,),
    )
    section_counts: dict[int, Counter] = defaultdict(Counter)
    concept_chunks: dict[int, set] = defaultdict(set)
    for m in mention_rows:
        sec = section_effective_of_chunk.get(m["chunk_id"])
        if sec is not None:
            section_counts[m["concept_id"]][sec] += 1
        concept_chunks[m["concept_id"]].add(m["chunk_id"])

    for concept_id, counter in section_counts.items():
        for sec, _n in counter.most_common(3):
            sec_node = f"sec:{sec}"
            if sec_node in nodes:
                add_edge(sec_node, f"con:{concept_id}", "mentions")

    for e in db.rows(conn, "SELECT * FROM concept_edges WHERE notebook_id = ?", (notebook_id,)):
        add_edge(f"con:{e['source_id']}", f"con:{e['target_id']}", e["relation"], confidence=e["confidence"])

    figlinks = db.rows(
        conn,
        "SELECT fl.figure_block_id, fl.chunk_id, b.type AS block_type FROM figure_links fl"
        " JOIN blocks b ON b.id = fl.figure_block_id JOIN chunks ch ON ch.id = fl.chunk_id"
        " WHERE ch.notebook_id = ?",
        (notebook_id,),
    )
    chunk_to_blocks: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for fl in figlinks:
        chunk_to_blocks[fl["chunk_id"]].append((fl["figure_block_id"], fl["block_type"]))
    seen_illustrated = set()
    for concept_id, chunk_ids in concept_chunks.items():
        for chunk_id in chunk_ids:
            for block_id, block_type in chunk_to_blocks.get(chunk_id, []):
                node_id = f"{'fig' if block_type == 'figure' else 'tab'}:{block_id}"
                key = (concept_id, node_id)
                if node_id in nodes and key not in seen_illustrated:
                    seen_illustrated.add(key)
                    add_edge(f"con:{concept_id}", node_id, "illustrated_by")

    # ---- topic tier ----
    topic_rows = db.rows(conn, "SELECT * FROM topics WHERE notebook_id = ?", (notebook_id,))
    for t in topic_rows:
        topic_node = f"topic:{t['id']}"
        nodes[topic_node] = {"id": topic_node, "type": "topic", "label": t["label"], "tier": "topic",
                              "summary": t["summary"], "ref": {"notebook_id": notebook_id}}
        for cid in json.loads(t["member_ids_json"] or "[]"):
            con_node = f"con:{cid}"
            if con_node in nodes:
                add_edge(con_node, topic_node, "member_of")

    return _finalize(nodes, edges, {
        "documents": len(documents),
        "sections": sum(1 for n in nodes.values() if n["type"] == "section"),
        "concepts": len(concepts),
        "topics": len(topic_rows),
    }, "notebook", notebook_id)


# ---------------------------------------------------------------------------------------------
# Archive payload
# ---------------------------------------------------------------------------------------------

def _build_archive_payload(conn, archive_id: int) -> dict:
    archive = db.one(conn, "SELECT * FROM archives WHERE id = ?", (archive_id,))
    if archive is None:
        raise ValueError(f"archive {archive_id} not found")
    notebooks = db.rows(conn, "SELECT id, name FROM notebooks WHERE archive_id = ?", (archive_id,))
    nb_ids = [n["id"] for n in notebooks]

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_seq = [0]

    def add_edge(source: str, target: str, type_: str, **kw) -> None:
        edge_seq[0] += 1
        e = {"id": f"e{edge_seq[0]}", "source": source, "target": target, "type": type_}
        e.update(kw)
        edges.append(e)

    documents_count = 0
    for nb in notebooks:
        nb_node = f"nb:{nb['id']}"
        nodes[nb_node] = {"id": nb_node, "type": "notebook", "label": nb["name"], "tier": "structure",
                           "ref": {"notebook_id": nb["id"], "archive_id": archive_id}}
        docs = db.rows(conn, "SELECT id, title FROM documents WHERE notebook_id = ?", (nb["id"],))
        documents_count += len(docs)
        for d in docs:
            doc_node = f"doc:{d['id']}"
            nodes[doc_node] = {"id": doc_node, "type": "document", "label": d["title"], "tier": "structure",
                                "ref": {"document_id": d["id"], "notebook_id": nb["id"], "archive_id": archive_id}}
            add_edge(nb_node, doc_node, "contains")

    aconcepts = db.rows(conn, "SELECT * FROM archive_concepts WHERE archive_id = ?", (archive_id,))
    mastery_map: dict[int, float] = {}
    if nb_ids:
        placeholders = ",".join("?" * len(nb_ids))
        for row in db.rows(
            conn,
            f"SELECT concept_id, score FROM concept_mastery WHERE concept_id IN"
            f" (SELECT id FROM concepts WHERE notebook_id IN ({placeholders}))",
            nb_ids,
        ):
            mastery_map[row["concept_id"]] = row["score"]

    concept_to_acon: dict[int, int] = {}
    for a in aconcepts:
        acon_node = f"acon:{a['id']}"
        member_ids = json.loads(a["member_concept_ids_json"] or "[]")
        for cid in member_ids:
            concept_to_acon[cid] = a["id"]
        scores = [mastery_map.get(cid, 0.0) for cid in member_ids]
        avg_mastery = sum(scores) / len(scores) if scores else 0.0
        nodes[acon_node] = {
            "id": acon_node, "type": "concept", "label": a["name"], "tier": "concept", "kind": a["kind"],
            "topic": f"topic:{a['topic_id']}" if a["topic_id"] else None, "mastery": avg_mastery,
            "ref": {"archive_concept_id": a["id"], "archive_id": archive_id},
        }

    if concept_to_acon:
        cids = list(concept_to_acon.keys())
        placeholders = ",".join("?" * len(cids))
        seen = set()
        for m in db.rows(
            conn,
            f"SELECT cm.concept_id, ch.document_id FROM concept_mentions cm JOIN chunks ch ON ch.id = cm.chunk_id"
            f" WHERE cm.concept_id IN ({placeholders})",
            cids,
        ):
            acon_node = f"acon:{concept_to_acon.get(m['concept_id'])}"
            doc_node = f"doc:{m['document_id']}"
            key = (doc_node, acon_node)
            if acon_node in nodes and doc_node in nodes and key not in seen:
                seen.add(key)
                add_edge(doc_node, acon_node, "mentions")

    if nb_ids:
        placeholders = ",".join("?" * len(nb_ids))
        agg: dict[tuple, str] = {}
        for e in db.rows(
            conn, f"SELECT source_id, target_id, relation, confidence FROM concept_edges"
            f" WHERE notebook_id IN ({placeholders})", nb_ids,
        ):
            sa, ta = concept_to_acon.get(e["source_id"]), concept_to_acon.get(e["target_id"])
            if sa is None or ta is None or sa == ta:
                continue
            agg.setdefault((sa, ta, e["relation"]), e["confidence"])
        for (sa, ta, relation), confidence in agg.items():
            add_edge(f"acon:{sa}", f"acon:{ta}", relation, confidence=confidence)

    topic_rows = db.rows(conn, "SELECT * FROM topics WHERE archive_id = ?", (archive_id,))
    for t in topic_rows:
        topic_node = f"topic:{t['id']}"
        nodes[topic_node] = {"id": topic_node, "type": "topic", "label": t["label"], "tier": "topic",
                              "summary": t["summary"], "ref": {"archive_id": archive_id}}
        for aid in json.loads(t["member_ids_json"] or "[]"):
            acon_node = f"acon:{aid}"
            if acon_node in nodes:
                add_edge(acon_node, topic_node, "member_of")

    return _finalize(nodes, edges, {
        "documents": documents_count, "notebooks": len(notebooks), "concepts": len(aconcepts),
        "topics": len(topic_rows),
    }, "archive", archive_id)


# ---------------------------------------------------------------------------------------------
# Shared finishing: layout + sizes + envelope
# ---------------------------------------------------------------------------------------------

def _finalize(nodes: dict[str, dict], edges: list[dict], stats: dict, kind: str, scope_id: int) -> dict:
    node_list = list(nodes.values())
    node_ids = [n["id"] for n in node_list]
    edge_pairs = [(e["source"], e["target"]) for e in edges]
    degree: Counter = Counter()
    for a, b in edge_pairs:
        degree[a] += 1
        degree[b] += 1
    pos = layout.compute_layout(node_ids, edge_pairs)
    for n in node_list:
        x, y = pos.get(n["id"], (0.0, 0.0))
        n["x"], n["y"] = x, y
        n["size"] = 4 + min(20, degree.get(n["id"], 0))
    return {
        "scope": {"kind": kind, "id": scope_id},
        "built_at": db.now_iso(),
        "nodes": node_list,
        "edges": edges,
        "stats": stats,
    }


# ---------------------------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------------------------

def notebook_graph(conn, notebook_id: int, *, force: bool = False) -> dict:
    key = f"notebook:{notebook_id}"
    if not force:
        cached = _cache_get(conn, key)
        if cached is not None:
            return cached
    payload = _build_notebook_payload(conn, notebook_id)
    _cache_put(conn, key, payload)
    return payload


def archive_graph(conn, archive_id: int, *, force: bool = False) -> dict:
    key = f"archive:{archive_id}"
    if not force:
        cached = _cache_get(conn, key)
        if cached is not None:
            return cached
    payload = _build_archive_payload(conn, archive_id)
    _cache_put(conn, key, payload)
    return payload


def rebuild_notebook_graph(conn, notebook_id: int, *, reextract: bool = False) -> dict:
    if reextract:
        _reextract_notebook(conn, notebook_id)
    topics.recompute_topics(conn, notebook_id)
    payload = _build_notebook_payload(conn, notebook_id)
    _cache_put(conn, f"notebook:{notebook_id}", payload)
    nb = db.one(conn, "SELECT archive_id FROM notebooks WHERE id = ?", (notebook_id,))
    if nb:
        merge.mark_dirty(conn, f"archive:{nb['archive_id']}")
    return payload


def rebuild_archive_graph(conn, archive_id: int) -> dict:
    merge.rebuild_archive_concepts(conn, archive_id)
    topics.recompute_archive_topics(conn, archive_id)
    payload = _build_archive_payload(conn, archive_id)
    _cache_put(conn, f"archive:{archive_id}", payload)
    return payload
