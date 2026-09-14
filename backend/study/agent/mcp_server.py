"""MCP tool server for the study agent: streamable HTTP, mounted at /mcp.

`study.index.search` (parse_scope, hybrid_search, chunk_in_scope, scope_notebook_ids) is owned by
another implementer and imported lazily so this module still imports cleanly before it exists; a
missing module surfaces as a normal tool error (the model sees it and can tell the student).

The installed `mcp` package is v2 (`mcp.server.mcpserver.MCPServer`, not the v1
`mcp.server.fastmcp.FastMCP` the SPEC names — v1's module raises ModuleNotFoundError with a
migration pointer). `MCPServer.streamable_http_app()` returns its own Starlette app whose lifespan
runs the session manager, but Starlette never dispatches the ASGI "lifespan" scope into a mounted
sub-app's own router — so mounting that app is not enough on its own, and a `StreamableHTTPSessionManager`
may only be `.run()` once. `mount(app)` therefore builds a *fresh* `MCPServer` (+ its ASGI app and
session manager) per call and stores it on `app.state`, rather than on a module-level singleton: a
process that builds more than one app — every test using `TestClient(create_app())`, one per test —
would otherwise hand a second app's `lifespan()` an already-`run()` manager shared with the first.
`lifespan(app)` reads that same instance back off `app.state` and runs its session manager for the
app's lifetime, per the module contract in app.py.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from .. import db

_INSTRUCTIONS = (
    "Study material tools, scoped to one notebook or archive. Cite chunks the tools return as "
    "[c:ID] and figures/tables as [f:ID], copied exactly from the tool output. Never invent an id."
)


def _page_label(page_start: int | None, page_end: int | None) -> str:
    if page_start is None:
        return ""
    if page_end is None or page_end == page_start:
        return str(page_start)
    return f"{page_start}-{page_end}"


def _format_chunk(
    chunk_id: int,
    document_title: str,
    page_start: int | None,
    page_end: int | None,
    heading_path: str | None,
    text: str,
    figures: list[dict] | None = None,
) -> str:
    header = f"[c:{chunk_id}] {document_title}"
    page = _page_label(page_start, page_end)
    if page:
        header += f" · p.{page}"
    if heading_path:
        header += f" · {heading_path}"
    lines = [header, text or ""]
    for fig in figures or []:
        label = fig.get("label") or "Figure"
        caption = fig.get("caption") or ""
        lines.append(f"[f:{fig['block_id']}] {label} — {caption}")
    return "\n".join(lines)


def _format_hit(hit: dict) -> str:
    return _format_chunk(
        hit["chunk_id"],
        hit.get("document_title", ""),
        hit.get("page_start"),
        hit.get("page_end"),
        hit.get("heading_path"),
        hit.get("text", ""),
        hit.get("figures"),
    )


def _figures_for_chunk(conn, chunk_id: int) -> list[dict]:
    return db.rows(
        conn,
        "SELECT b.id AS block_id, b.label AS label, b.caption AS caption "
        "FROM figure_links fl JOIN blocks b ON b.id = fl.figure_block_id "
        "WHERE fl.chunk_id = ? GROUP BY b.id",
        (chunk_id,),
    )


def _notebook_ids_for_scope(conn, scope: str) -> list[int]:
    from study.index.search import parse_scope, scope_notebook_ids

    kind, sid = parse_scope(scope)
    return scope_notebook_ids(conn, kind, sid)


def search(scope: str, query: str, k: int = 8) -> str:
    from study.index.search import hybrid_search, parse_scope

    parse_scope(scope)
    conn = db.connect()
    try:
        hits = hybrid_search(conn, scope, query, k)
    finally:
        conn.close()
    if not hits:
        return "No matching passages found."
    return "\n\n".join(_format_hit(h) for h in hits)


def read_context(scope: str, chunk_id: int, window: int = 1) -> str:
    from study.index.search import chunk_in_scope, parse_scope

    parse_scope(scope)
    conn = db.connect()
    try:
        if not chunk_in_scope(conn, scope, chunk_id):
            return f"c:{chunk_id} is not in scope {scope}."
        chunk = db.one(conn, "SELECT * FROM chunks WHERE id = ?", (chunk_id,))
        if chunk is None:
            return f"No such chunk c:{chunk_id}."
        doc = db.one(conn, "SELECT title FROM documents WHERE id = ?", (chunk["document_id"],))
        rows = db.rows(
            conn,
            "SELECT * FROM chunks WHERE document_id = ? AND seq BETWEEN ? AND ? ORDER BY seq",
            (chunk["document_id"], chunk["seq"] - window, chunk["seq"] + window),
        )
        parts = [
            _format_chunk(
                r["id"],
                doc["title"] if doc else "",
                r["page_start"],
                r["page_end"],
                r["heading_path"],
                r["text"],
                _figures_for_chunk(conn, r["id"]),
            )
            for r in rows
        ]
        return "\n\n".join(parts)
    finally:
        conn.close()


def get_figure(scope: str, block_id: int) -> str:
    conn = db.connect()
    try:
        nb_ids = set(_notebook_ids_for_scope(conn, scope))
        block = db.one(conn, "SELECT * FROM blocks WHERE id = ?", (block_id,))
        if block is None or block["type"] not in ("figure", "table"):
            return f"No such figure/table f:{block_id}."
        doc = db.one(conn, "SELECT notebook_id FROM documents WHERE id = ?", (block["document_id"],))
        if doc is None or doc["notebook_id"] not in nb_ids:
            return f"f:{block_id} is not in scope {scope}."
        lines = [f"[f:{block_id}] {block.get('label') or ''}".strip()]
        if block.get("caption"):
            lines.append(f"Caption: {block['caption']}")
        if block.get("description"):
            lines.append(f"Description: {block['description']}")
        if block["type"] == "table":
            if block.get("text"):
                lines.append(f"Table text: {block['text']}")
            if block.get("table_html"):
                lines.append(f"Table HTML: {block['table_html']}")
        return "\n".join(lines)
    finally:
        conn.close()


def _find_concept(conn, nb_ids: list[int], name: str) -> dict | None:
    if not nb_ids:
        return None
    placeholders = ",".join("?" * len(nb_ids))
    norm = " ".join(name.strip().lower().split())
    row = db.one(
        conn,
        f"SELECT * FROM concepts WHERE notebook_id IN ({placeholders}) AND lower(norm_name) = ? LIMIT 1",
        (*nb_ids, norm),
    )
    if row:
        return row
    row = db.one(
        conn,
        f"SELECT * FROM concepts WHERE notebook_id IN ({placeholders}) AND lower(name) = ? LIMIT 1",
        (*nb_ids, norm),
    )
    if row:
        return row
    return db.one(
        conn,
        f"SELECT * FROM concepts WHERE notebook_id IN ({placeholders}) AND lower(name) LIKE ? LIMIT 1",
        (*nb_ids, f"%{norm}%"),
    )


def _format_concept(conn, concept: dict) -> str:
    cid = concept["id"]
    lines = [f"Concept: {concept['name']} ({concept.get('kind') or 'concept'})"]
    if concept.get("summary"):
        lines.append(concept["summary"])
    rel_rows = db.rows(
        conn,
        "SELECT ce.relation AS relation, ce.confidence AS confidence, 'out' AS direction,"
        " c2.name AS other_name "
        "FROM concept_edges ce JOIN concepts c2 ON c2.id = ce.target_id WHERE ce.source_id = ? "
        "UNION ALL "
        "SELECT ce.relation AS relation, ce.confidence AS confidence, 'in' AS direction,"
        " c2.name AS other_name "
        "FROM concept_edges ce JOIN concepts c2 ON c2.id = ce.source_id WHERE ce.target_id = ?",
        (cid, cid),
    )
    if rel_rows:
        lines.append("Relations:")
        for r in rel_rows:
            arrow = "->" if r["direction"] == "out" else "<-"
            lines.append(f"  {arrow} {r['relation']} {arrow} {r['other_name']} ({r['confidence']})")
    mention_rows = db.rows(
        conn,
        "SELECT cm.chunk_id AS chunk_id, cm.evidence AS evidence, d.title AS document_title,"
        " ch.page_start AS page_start "
        "FROM concept_mentions cm JOIN chunks ch ON ch.id = cm.chunk_id"
        " JOIN documents d ON d.id = ch.document_id "
        "WHERE cm.concept_id = ? LIMIT 8",
        (cid,),
    )
    if mention_rows:
        lines.append("Mentions:")
        for m in mention_rows:
            page = f" p.{m['page_start']}" if m.get("page_start") else ""
            evidence = f" — {m['evidence']}" if m.get("evidence") else ""
            lines.append(f"  [c:{m['chunk_id']}] {m['document_title']}{page}{evidence}")
    return "\n".join(lines)


def concept_lookup(scope: str, name: str) -> str:
    conn = db.connect()
    try:
        nb_ids = _notebook_ids_for_scope(conn, scope)
        concept = _find_concept(conn, nb_ids, name)
        if concept is None:
            return f"No concept named {name!r} found in {scope}."
        return _format_concept(conn, concept)
    finally:
        conn.close()


def list_documents(scope: str) -> str:
    conn = db.connect()
    try:
        nb_ids = _notebook_ids_for_scope(conn, scope)
        if not nb_ids:
            return f"No notebooks in scope {scope}."
        placeholders = ",".join("?" * len(nb_ids))
        docs = db.rows(
            conn,
            f"SELECT id, title FROM documents WHERE notebook_id IN ({placeholders}) ORDER BY id",
            tuple(nb_ids),
        )
        if not docs:
            return f"No documents in scope {scope}."
        lines = []
        for d in docs:
            lines.append(f"{d['title']} (doc:{d['id']})")
            headings = db.rows(
                conn,
                "SELECT text FROM blocks WHERE document_id = ? AND type = 'heading' AND level <= 2"
                " ORDER BY seq",
                (d["id"],),
            )
            lines.extend(f"  - {h['text']}" for h in headings)
        return "\n".join(lines)
    finally:
        conn.close()


# (fn, description) — description text is what the model sees in its tool catalog, so each one
# spells out the exact citation marker format.
_TOOL_SPECS: list[tuple[object, str]] = [
    (
        search,
        "Search the study material in `scope` (\"notebook:<id>\" or \"archive:<id>\") for passages "
        "relevant to `query`. Returns up to `k` chunks formatted as '[c:ID] Document Title · p.X "
        "· heading path' followed by the chunk text, with any linked figures/tables listed as "
        "'[f:ID] label — caption'. Cite chunks with their exact [c:ID] marker and figures with "
        "their exact [f:ID] marker; never invent an id. Call this before answering any factual "
        "question.",
    ),
    (
        read_context,
        "Return chunk `chunk_id` (must be in `scope`) plus `window` neighbouring chunks before and "
        "after it in the same document, for extra context around a search hit. Formatted the same way "
        "as `search`: each chunk as '[c:ID] Document Title · p.X · heading path' then its "
        "text, with linked figures as '[f:ID] label — caption'. Cite ids exactly as shown.",
    ),
    (
        get_figure,
        "Look up figure or table block `block_id` (must be in `scope`) and return its label, caption, "
        "description, and (for a table) its text/HTML. Cite it in your answer with its exact [f:ID] "
        "marker, e.g. [f:45].",
    ),
    (
        concept_lookup,
        "Look up a concept by name in `scope` and return its summary, its relations to other "
        "concepts, and the chunks that mention it as '[c:ID] Document · p.X — evidence'. "
        "Cite mention chunks with their exact [c:ID] marker.",
    ),
    (
        list_documents,
        "List the documents in `scope` with their titles and top-level headings, so you know what "
        "material is available before searching.",
    ),
]


def _build_server() -> MCPServer:
    server = MCPServer("study", instructions=_INSTRUCTIONS)
    for fn, description in _TOOL_SPECS:
        server.add_tool(fn, description=description)
    return server


def mount(app) -> None:
    server = _build_server()
    # Building the ASGI app also creates this server's `StreamableHTTPSessionManager` (see the
    # module docstring for why a fresh one per app matters); stash the server on `app.state` so
    # `lifespan(app)` below runs *this* app's manager instead of some other app's.
    sub_app = server.streamable_http_app(streamable_http_path="/", stateless_http=True)
    app.state.study_mcp_server = server
    app.mount("/mcp", sub_app)


@asynccontextmanager
async def lifespan(app):
    server = app.state.study_mcp_server
    async with server.session_manager.run():
        yield
