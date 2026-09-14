"""Study guide generation (SPEC §9): markdown, citing `[c:ID]`."""
from __future__ import annotations

import json
import re

from .. import db, llm
from . import context
from .flashcards import require_llm

_SYSTEM = f"""\
Write a concise markdown study guide from the material given. Use headings to organize topics and
cite sources inline as [c:ID] right after the sentence they support, using ONLY the chunk ids given
in the material (never invent one).
{llm.UNTRUSTED_RULE}
"""


def generate(conn, notebook_id: int, scope: dict) -> dict:
    require_llm()
    chunks = context.resolve_scope_chunks(conn, scope)
    valid_ids = {c["id"] for c in chunks}
    user = context.build_context_text(chunks)
    llm_client = llm.get_llm()
    content = llm_client.chat_text(task="guide", system=_SYSTEM, user=user, max_tokens=2000)
    content = _strip_unknown_citations(content, valid_ids)
    title = _first_heading(content) or "Study Guide"

    cur = conn.execute(
        "INSERT INTO guides (notebook_id, scope_json, title, content_md) VALUES (?,?,?,?)",
        (notebook_id, json.dumps(scope), title, content),
    )
    return _row(conn, cur.lastrowid)


def list_guides(conn, notebook_id: int) -> list[dict]:
    return db.rows(
        conn, "SELECT id, title, content_md, created_at FROM guides WHERE notebook_id = ? ORDER BY created_at DESC",
        (notebook_id,),
    )


def _row(conn, guide_id: int) -> dict:
    return db.one(conn, "SELECT id, title, content_md, created_at FROM guides WHERE id = ?", (guide_id,))


def _strip_unknown_citations(content: str, valid_ids: set[int]) -> str:
    def repl(m: re.Match) -> str:
        return m.group(0) if int(m.group(1)) in valid_ids else ""
    return re.sub(r"\[c:(\d+)\]", repl, content)


def _first_heading(content: str) -> str | None:
    m = re.search(r"^#+\s*(.+)$", content, re.M)
    return m.group(1).strip() if m else None


@llm.fake_handler("guide")
def _fake_guide(system: str, user: str) -> str:
    lines = ["# Study Guide", ""]
    for m in re.finditer(r'<untrusted_source id="chunk:(\d+)">\s*(.*?)\s*</untrusted_source>', user, re.S):
        chunk_id, text = int(m.group(1)), m.group(2)
        lines.append(f"## Chunk {chunk_id}")
        snippet = " ".join(text.split())[:200]
        lines.append(f"{snippet} [c:{chunk_id}]")
        lines.append("")
    return "\n".join(lines)
