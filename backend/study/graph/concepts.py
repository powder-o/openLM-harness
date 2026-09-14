"""Concept extraction for the concept tier (SPEC §8/§10).

Batches a document's text chunks (~2500 tokens per batch), asks the LLM for concepts + relations
using a graphify-style prompt (JSON-only, EXTRACTED/INFERRED/AMBIGUOUS confidence, untrusted-source
rule), runs batches concurrently, and validates the output defensively. A `@fake_handler("concepts")`
makes this exercisable offline (STUDY_LLM_FAKE=1): it deterministically pulls capitalized phrases out
of the chunk text instead of calling a real model.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .. import db, llm

log = logging.getLogger("study.graph.concepts")

CONCEPT_KINDS = {"concept", "definition", "theorem", "method", "person", "term", "formula", "event"}
RELATION_KINDS = {
    "prerequisite_of", "part_of", "example_of", "contrasts_with", "causes", "uses", "related_to",
}
CONFIDENCE_KINDS = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}

_BATCH_TOKEN_BUDGET = 2500
_MAX_CONCEPTS_PER_BATCH = 12
_WORKERS = 4

_SYSTEM = f"""\
You are a study-app concept extraction agent. Extract a small knowledge-graph fragment from the \
document chunks given (an excerpt of a textbook or notes), for a student's study graph.
Output ONLY valid JSON - no explanation, no markdown fences, no preamble.

Concept kinds: concept | definition | theorem | method | person | term | formula | event.
Relation kinds: prerequisite_of | part_of | example_of | contrasts_with | causes | uses | related_to.
`prerequisite_of` means the SOURCE concept must be understood before the TARGET concept.

Confidence rules:
- EXTRACTED: the relationship is stated explicitly in the text
- INFERRED: a reasonable inference from context, not stated outright
- AMBIGUOUS: uncertain - flag for review, do not omit

{llm.UNTRUSTED_RULE}

Extract at most {_MAX_CONCEPTS_PER_BATCH} of the most important concepts across all chunks given. \
For each concept list only the chunk ids it is actually discussed in, plus a short verbatim evidence \
snippet. Only emit relations between concepts you extracted.

Output exactly this JSON schema:
{{"concepts": [{{"name": "...", "kind": "concept|definition|theorem|method|person|term|formula|event",
  "summary": "...", "chunk_ids": [1, 2], "evidence": "..."}}],
 "relations": [{{"source": "concept name", "target": "concept name",
  "relation": "prerequisite_of|part_of|example_of|contrasts_with|causes|uses|related_to",
  "confidence": "EXTRACTED|INFERRED|AMBIGUOUS", "evidence": "...", "chunk_id": 1}}]}}
"""


def _token_estimate(chunk: dict) -> int:
    return chunk.get("token_count") or max(1, len(chunk["text"]) // 4)


def _batches(chunks: list[dict]) -> list[list[dict]]:
    batches: list[list[dict]] = []
    cur: list[dict] = []
    cur_tokens = 0
    for c in chunks:
        t = _token_estimate(c)
        if cur and cur_tokens + t > _BATCH_TOKEN_BUDGET:
            batches.append(cur)
            cur, cur_tokens = [], 0
        cur.append(c)
        cur_tokens += t
    if cur:
        batches.append(cur)
    return batches


def _build_user_prompt(batch: list[dict]) -> str:
    return "\n\n".join(llm.wrap_untrusted(f"chunk:{c['id']}", c["text"]) for c in batch)


def _validate(raw: dict, valid_chunk_ids: set[int]) -> dict:
    concepts = []
    for c in (raw.get("concepts") or []) if isinstance(raw, dict) else []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        chunk_ids = [cid for cid in (c.get("chunk_ids") or []) if cid in valid_chunk_ids]
        if not chunk_ids:
            continue
        kind = c.get("kind") if c.get("kind") in CONCEPT_KINDS else "concept"
        concepts.append({
            "name": name, "kind": kind, "summary": str(c.get("summary") or ""),
            "chunk_ids": chunk_ids, "evidence": str(c.get("evidence") or ""),
        })
    relations = []
    for r in (raw.get("relations") or []) if isinstance(raw, dict) else []:
        if not isinstance(r, dict):
            continue
        source = str(r.get("source") or "").strip()
        target = str(r.get("target") or "").strip()
        if not source or not target or source == target:
            continue
        relation = r.get("relation") if r.get("relation") in RELATION_KINDS else "related_to"
        confidence = r.get("confidence") if r.get("confidence") in CONFIDENCE_KINDS else "INFERRED"
        chunk_id = r.get("chunk_id")
        chunk_id = chunk_id if chunk_id in valid_chunk_ids else None
        relations.append({
            "source": source, "target": target, "relation": relation, "confidence": confidence,
            "evidence": str(r.get("evidence") or ""), "chunk_id": chunk_id,
        })
    return {"concepts": concepts, "relations": relations}


def extract_for_document(conn, document_id: int, progress: Callable[[str], None] = lambda _d: None) -> dict:
    """Extract concepts + relations for one document's text chunks. Never raises for LLM errors -
    a failed batch just contributes nothing (logged); the caller decides whether to give up entirely."""
    chunks = db.rows(
        conn, "SELECT id, text, token_count, heading_path FROM chunks WHERE document_id = ? AND kind = 'text'"
        " ORDER BY seq", (document_id,),
    )
    chunks = [c for c in chunks if c["text"].strip()]
    if not chunks:
        progress("concepts 0/0 batches")
        return {"concepts": [], "relations": []}

    batches = _batches(chunks)
    llm_client = llm.get_llm()
    results: list[dict] = [{"concepts": [], "relations": []} for _ in batches]
    done = 0

    def run(batch: list[dict]) -> dict:
        user = _build_user_prompt(batch)
        raw = llm_client.chat_json(task="concepts", system=_SYSTEM, user=user, max_tokens=3000)
        return _validate(raw, {c["id"] for c in batch})

    with ThreadPoolExecutor(max_workers=min(_WORKERS, len(batches))) as ex:
        futures = {ex.submit(run, batch): i for i, batch in enumerate(batches)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception:
                log.exception(
                    "concept extraction batch %d/%d failed for document %d; skipping it",
                    i + 1, len(batches), document_id,
                )
            done += 1
            progress(f"concepts {done}/{len(batches)} batches")

    concepts: list[dict] = []
    relations: list[dict] = []
    for r in results:
        concepts.extend(r["concepts"])
        relations.extend(r["relations"])
    return {"concepts": concepts, "relations": relations}


# --------------------------------------------------------------------------------------------------
# Fake handler: deterministic capitalized-phrase extraction so tests exercise this offline.
# --------------------------------------------------------------------------------------------------

_CHUNK_RE = re.compile(r'<untrusted_source id="chunk:(\d+)">\s*(.*?)\s*</untrusted_source>', re.S)
# Title-case phrases of 2+ words, allowing "of"/"the"/"and" as internal connectors
# (e.g. "First Law of Thermodynamics", "Second Law of Thermodynamics").
_MULTI_WORD_RE = re.compile(
    r"\b[A-Z][a-zA-Z]+(?:\s+(?:of|the|and)\s+[A-Z][a-zA-Z]+|\s+[A-Z][a-zA-Z]+)+\b"
)
_LEADING_STOPWORDS = {
    "The", "A", "An", "This", "That", "These", "Those", "It", "Its", "We", "As", "Understanding",
}
# Known single-word terms worth extracting on their own even when not sentence-capitalized
# (proper nouns / defined terms); matched case-insensitively but always emitted with this spelling.
_KNOWN_SINGLE_WORDS = ("Entropy", "Carnot", "Boltzmann")


def _clean_phrase(phrase: str) -> str | None:
    words = phrase.split()
    while words and (words[0] in _LEADING_STOPWORDS or words[0].islower()):
        words.pop(0)
    if len(words) < 2:
        return None
    return " ".join(words)


def _guess_kind(name: str) -> str:
    if name in ("Carnot", "Boltzmann"):
        return "person"
    if "Law" in name or "Theorem" in name:
        return "theorem"
    if name == "Entropy":
        return "definition"
    return "concept"


def _canonicalize(phrase: str, universe: list[str]) -> str:
    """A short phrase that is a word-prefix of a longer one found in the same text is the same
    concept referred to more briefly (e.g. "the Second Law" later in a paragraph that opened with
    "the Second Law of Thermodynamics") - collapse it to the longer, canonical form."""
    words = phrase.split()
    for other in universe:
        other_words = other.split()
        if len(other_words) > len(words) and other_words[: len(words)] == words:
            return other
    return phrase


def _dedupe_prefixes(phrases: list[str]) -> list[str]:
    result: list[str] = []
    for p in phrases:
        canon = _canonicalize(p, phrases)
        if canon not in result:
            result.append(canon)
    return result


def _phrases_in(text: str) -> list[str]:
    """Deterministic capitalized-phrase extraction used by the fake handler, in order found."""
    found: list[str] = []
    for raw in _MULTI_WORD_RE.findall(text):
        cleaned = _clean_phrase(raw)
        if cleaned and cleaned not in found:
            found.append(cleaned)
    for w in _KNOWN_SINGLE_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE) and w not in found:
            found.append(w)
    return _dedupe_prefixes(found)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@llm.fake_handler("concepts")
def _fake_concepts(system: str, user: str) -> dict:
    concepts: dict[str, dict] = {}
    relations: list[dict] = []
    for m in _CHUNK_RE.finditer(user):
        chunk_id = int(m.group(1))
        text = m.group(2)
        names = _phrases_in(text)
        for n in names:
            c = concepts.setdefault(n, {
                "name": n, "kind": _guess_kind(n), "summary": f"{n} (from chunk {chunk_id}).",
                "chunk_ids": [], "evidence": text[:200],
            })
            if chunk_id not in c["chunk_ids"]:
                c["chunk_ids"].append(chunk_id)

        if "prerequisite" in text.lower():
            # Find the sentence that actually states the prerequisite relationship (a chunk can
            # mention several concepts without all of them relating to each other) and read the
            # two concepts off of it in order, canonicalized against the whole chunk's phrases so
            # a short in-sentence mention ("the Second Law") resolves to the same node as its
            # canonical form ("Second Law of Thermodynamics") elsewhere in the chunk.
            for sentence in _SENTENCE_SPLIT_RE.split(text):
                if "prerequisite" not in sentence.lower():
                    continue
                local: list[str] = []
                for p in _phrases_in(sentence):
                    canon = _canonicalize(p, names)
                    if canon not in local:
                        local.append(canon)
                if len(local) >= 2:
                    relations.append({
                        "source": local[0], "target": local[1], "relation": "prerequisite_of",
                        "confidence": "EXTRACTED", "evidence": sentence.strip()[:200], "chunk_id": chunk_id,
                    })
    return {"concepts": list(concepts.values()), "relations": relations}
