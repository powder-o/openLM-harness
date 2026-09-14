"""Concept merging (SPEC §8): within a notebook, and across a whole archive.

A new concept merges into an existing one (same notebook, or same archive for `archive_concepts`)
when its normalized name matches, rapidfuzz `token_sort_ratio >= 92`, or embedding cosine `>= 0.9`.
This is what makes "Entropy" mentioned in two different documents (or two different notebooks in the
same archive) become a single node.
"""
from __future__ import annotations

import json
import re

from rapidfuzz import fuzz

from .. import db
from ..index import embed

FUZZY_THRESHOLD = 92.0
EMBED_THRESHOLD = 0.9


def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _is_match(norm: str, name: str, vec, existing: dict) -> bool:
    if norm == existing["norm_name"]:
        return True
    if fuzz.token_sort_ratio(name, existing["name"]) >= FUZZY_THRESHOLD:
        return True
    other_vec = existing.get("_vec")
    if vec is not None and other_vec is not None and embed.cosine(vec, other_vec) >= EMBED_THRESHOLD:
        return True
    return False


def mark_dirty(conn, key: str) -> None:
    conn.execute(
        "INSERT INTO graph_cache (key, dirty) VALUES (?, 1) ON CONFLICT(key) DO UPDATE SET dirty = 1", (key,)
    )


def merge_concepts(conn, notebook_id: int, extracted: list[dict]) -> dict[str, int]:
    """Merge extracted concepts (name/kind/summary/chunk_ids/evidence) into notebook `concepts`.

    Returns {extracted_name: concept_id}; creates concept_mentions rows for the given chunk ids.
    """
    existing: list[dict] = []
    for r in db.rows(conn, "SELECT * FROM concepts WHERE notebook_id = ?", (notebook_id,)):
        r["_vec"] = embed.from_blob(r["embedding"]) if r["embedding"] else None
        existing.append(r)

    name_to_id: dict[str, int] = {}
    for c in extracted:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        nn = norm_name(name)
        summary = c.get("summary") or ""
        # Embed the bare name, not "name: summary": with real embeddings, boilerplate-ish
        # summaries ("First Law of Thermodynamics (from chunk 1)." vs "Second Law of
        # Thermodynamics (from chunk 1).") pushed cosine similarity for genuinely distinct
        # concepts above EMBED_THRESHOLD (measured ~0.94 vs ~0.87 for the name alone), causing
        # false merges. Matching on the name keeps the fuzzy/embedding signals about concept
        # identity rather than incidental similarity in how two summaries happen to be phrased.
        vec = embed.embed_texts([name])[0]

        match = next((e for e in existing if _is_match(nn, name, vec, e)), None)
        if match is None:
            cur = conn.execute(
                "INSERT INTO concepts (notebook_id, name, norm_name, kind, summary, aliases_json, embedding)"
                " VALUES (?,?,?,?,?,?,?)",
                (notebook_id, name, nn, c.get("kind", "concept"), summary, "[]", embed.to_blob(vec)),
            )
            concept_id = cur.lastrowid
            match = {"id": concept_id, "name": name, "norm_name": nn, "_vec": vec, "summary": summary,
                     "aliases_json": "[]"}
            existing.append(match)
        else:
            concept_id = match["id"]
            aliases = json.loads(match.get("aliases_json") or "[]")
            if name != match["name"] and name not in aliases:
                aliases.append(name)
                conn.execute("UPDATE concepts SET aliases_json = ? WHERE id = ?", (json.dumps(aliases), concept_id))
                match["aliases_json"] = json.dumps(aliases)
            if not match.get("summary") and summary:
                conn.execute("UPDATE concepts SET summary = ? WHERE id = ?", (summary, concept_id))
                match["summary"] = summary

        name_to_id[name] = concept_id
        for chunk_id in c.get("chunk_ids") or []:
            conn.execute(
                "INSERT OR IGNORE INTO concept_mentions (concept_id, chunk_id, evidence) VALUES (?,?,?)",
                (concept_id, chunk_id, c.get("evidence", "")),
            )
    return name_to_id


def apply_extraction(conn, notebook_id: int, result: dict) -> dict[str, int]:
    """Merge extracted concepts and insert their relations as `concept_edges`. Returns name->id map."""
    name_to_id = merge_concepts(conn, notebook_id, result.get("concepts") or [])
    for rel in result.get("relations") or []:
        source_id = name_to_id.get(rel["source"])
        target_id = name_to_id.get(rel["target"])
        if source_id is None or target_id is None or source_id == target_id:
            continue
        conn.execute(
            "INSERT INTO concept_edges (notebook_id, source_id, target_id, relation, confidence, evidence, chunk_id)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(source_id, target_id, relation) DO UPDATE SET"
            " confidence = excluded.confidence, evidence = excluded.evidence, chunk_id = excluded.chunk_id",
            (notebook_id, source_id, target_id, rel["relation"], rel["confidence"], rel.get("evidence", ""),
             rel.get("chunk_id")),
        )
    return name_to_id


def rebuild_archive_concepts(conn, archive_id: int) -> None:
    """Rebuild `archive_concepts` from every notebook's concepts in this archive (full rebuild)."""
    nb_ids = [n["id"] for n in db.rows(conn, "SELECT id FROM notebooks WHERE archive_id = ?", (archive_id,))]
    conn.execute("DELETE FROM archive_concepts WHERE archive_id = ?", (archive_id,))
    if not nb_ids:
        return

    placeholders = ",".join("?" * len(nb_ids))
    concepts = db.rows(conn, f"SELECT * FROM concepts WHERE notebook_id IN ({placeholders})", nb_ids)

    existing: list[dict] = []
    for c in concepts:
        vec = embed.from_blob(c["embedding"]) if c["embedding"] else None
        nn = c["norm_name"]
        match = next((e for e in existing if _is_match(nn, c["name"], vec, e)), None)
        if match is None:
            cur = conn.execute(
                "INSERT INTO archive_concepts (archive_id, name, norm_name, kind, summary, member_concept_ids_json)"
                " VALUES (?,?,?,?,?,?)",
                (archive_id, c["name"], nn, c["kind"], c["summary"], json.dumps([c["id"]])),
            )
            existing.append({"id": cur.lastrowid, "name": c["name"], "norm_name": nn, "_vec": vec,
                              "member_ids": [c["id"]]})
        else:
            match["member_ids"].append(c["id"])
            conn.execute(
                "UPDATE archive_concepts SET member_concept_ids_json = ? WHERE id = ?",
                (json.dumps(match["member_ids"]), match["id"]),
            )
    mark_dirty(conn, f"archive:{archive_id}")
