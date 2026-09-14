"""Turn orchestration: send a user turn to dsh, translate its notifications into SSE-shaped
events, and build citations/highlight for the saved assistant message.

`run_turn` is synchronous and blocking by design — the API layer runs it on a worker thread
(`loop.run_in_executor`) and receives events through the `emit` callback, which must be safe to
call from that thread (see study/api/chat.py, which uses `loop.call_soon_threadsafe`).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable

from .. import config, db
from . import harness as harness_module

log = logging.getLogger("study.agent.chat")

_MARKER_RE = re.compile(r"\[(c|f):(\d+)\]")
_MCP_TOOL_PREFIX = "mcp__study__"
_DEFAULT_TITLE = "New chat"
_SNIPPET_LEN = 200
_SUMMARY_LEN = 160


# --------------------------------------------------------------------------
# Citation stripping and building (pure; covered directly by tests/test_citations.py)
# --------------------------------------------------------------------------


def strip_and_collect_markers(
    text: str, retrieved_chunk_ids: set[int], retrieved_block_ids: set[int]
) -> tuple[str, list[tuple[str, int]]]:
    """Remove [c:ID]/[f:ID] markers not present in this turn's tool results.

    Returns the cleaned text and the ids that survived, in first-appearance order, each unique
    (kind, id) pair listed once.
    """
    kept: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    def repl(m: re.Match) -> str:
        kind, num = m.group(1), int(m.group(2))
        allowed = retrieved_chunk_ids if kind == "c" else retrieved_block_ids
        if num not in allowed:
            return ""
        if (kind, num) not in seen:
            seen.add((kind, num))
            kept.append((kind, num))
        return m.group(0)

    clean = _MARKER_RE.sub(repl, text)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    clean = re.sub(r"[ \t]+([.,;:!?])", r"\1", clean)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip(), kept


def _chunk_citation(conn, chunk_id: int) -> dict | None:
    chunk = db.one(conn, "SELECT * FROM chunks WHERE id = ?", (chunk_id,))
    if chunk is None:
        return None
    doc = db.one(conn, "SELECT title, kind FROM documents WHERE id = ?", (chunk["document_id"],))
    block_ids = json.loads(chunk["block_ids_json"] or "[]")
    boxes: list[dict] = []
    if block_ids:
        placeholders = ",".join("?" * len(block_ids))
        for b in db.rows(
            conn, f"SELECT page, bbox_json FROM blocks WHERE id IN ({placeholders})", tuple(block_ids)
        ):
            if b["page"] is not None and b["bbox_json"]:
                boxes.append({"page": b["page"], "bbox": json.loads(b["bbox_json"])})
    text = chunk["text"] or ""
    snippet = text if len(text) <= _SNIPPET_LEN else text[: _SNIPPET_LEN - 1].rstrip() + "…"
    return {
        "marker": f"c:{chunk_id}",
        "kind": "chunk",
        "chunk_id": chunk_id,
        "document_id": chunk["document_id"],
        "document_title": doc["title"] if doc else "",
        "document_kind": doc["kind"] if doc else "",
        "page": chunk["page_start"],
        "heading_path": chunk["heading_path"],
        "snippet": snippet,
        "boxes": boxes,
    }


def _figure_citation(conn, block_id: int) -> dict | None:
    block = db.one(conn, "SELECT * FROM blocks WHERE id = ?", (block_id,))
    if block is None:
        return None
    doc = db.one(conn, "SELECT title FROM documents WHERE id = ?", (block["document_id"],))
    bbox = json.loads(block["bbox_json"]) if block.get("bbox_json") else None
    return {
        "marker": f"f:{block_id}",
        "kind": "figure",
        "block_id": block_id,
        "document_id": block["document_id"],
        "document_title": doc["title"] if doc else "",
        "label": block.get("label"),
        "caption": block.get("caption"),
        "image_url": f"/api/blocks/{block_id}/image",
        "page": block["page"],
        "bbox": bbox,
    }


def build_citations(conn, kept_markers: list[tuple[str, int]]) -> list[dict]:
    out = []
    for kind, num in kept_markers:
        citation = _chunk_citation(conn, num) if kind == "c" else _figure_citation(conn, num)
        if citation is not None:
            out.append(citation)
    return out


def _build_highlight(conn, scope: str, chunk_ids: list[int]) -> dict:
    if not chunk_ids:
        return {"primary": [], "related": []}
    try:
        from study.graph.highlight import highlight_for_chunks
    except ModuleNotFoundError:
        return {"primary": [], "related": []}
    try:
        return highlight_for_chunks(conn, scope, chunk_ids)
    except Exception:
        log.exception("highlight_for_chunks failed; falling back to empty highlight")
        return {"primary": [], "related": []}


# --------------------------------------------------------------------------
# Notification -> SSE event translation
# --------------------------------------------------------------------------


def _short_tool_name(name: str) -> str:
    return name[len(_MCP_TOOL_PREFIX):] if name.startswith(_MCP_TOOL_PREFIX) else name


def _summarize(text: str, limit: int = _SUMMARY_LEN) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1].rstrip() + "…"


def _tool_result_text_and_error(data: dict) -> tuple[str, bool]:
    message = data.get("message") or {}
    content = message.get("content") or []
    parts: list[str] = []
    is_error = bool(data.get("error"))
    for block in content:
        if not isinstance(block, dict):
            continue
        is_error = is_error or bool(block.get("isError"))
        for part in block.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
    return "\n".join(parts), is_error


def _tool_result_call_id(data: dict) -> str | None:
    message = data.get("message") or {}
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("toolCallId"):
            return block["toolCallId"]
    return None


def _assistant_message_text(data: dict) -> str:
    message = data.get("message") or {}
    return "".join(
        str(b.get("text") or "") for b in message.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"
    )


class _TurnState:
    def __init__(self, emit: Callable[[str, dict], None]) -> None:
        self.emit = emit
        self.tool_calls: list[dict] = []
        self.tool_names: dict[str, str] = {}
        self.retrieved_chunk_ids: dict[int, None] = {}
        self.retrieved_block_ids: dict[int, None] = {}

    def _record_markers(self, text: str) -> None:
        for m in re.finditer(r"\[c:(\d+)\]", text):
            self.retrieved_chunk_ids.setdefault(int(m.group(1)), None)
        for m in re.finditer(r"\[f:(\d+)\]", text):
            self.retrieved_block_ids.setdefault(int(m.group(1)), None)

    def handle(self, notification) -> None:
        if notification.method != "session.event":
            return
        event = notification.payload.get("event")
        if not isinstance(event, dict):
            return
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "tool/call":
            self._on_tool_call(data)
        elif etype == "tool/result":
            self._on_tool_result(data)
        elif etype == "assistant/message":
            self._on_assistant_message(data)

    def _on_tool_call(self, data: dict) -> None:
        call_id = data.get("callId")
        raw_name = data.get("name") or ""
        raw_args = data.get("arguments") or ""
        try:
            args = json.loads(raw_args) if raw_args else {}
        except (TypeError, ValueError):
            args = {"raw": raw_args}
        name = _short_tool_name(raw_name)
        self.tool_names[call_id] = name
        self.tool_calls.append({"id": call_id, "name": name, "arguments": args})
        self.emit("tool_call", {"id": call_id, "name": name, "arguments": args})

    def _on_tool_result(self, data: dict) -> None:
        call_id = _tool_result_call_id(data)
        text, is_error = _tool_result_text_and_error(data)
        self._record_markers(text)
        summary = _summarize(text)
        name = self.tool_names.get(call_id, "")
        for tc in self.tool_calls:
            if tc["id"] == call_id:
                tc["summary"] = summary
                tc["is_error"] = is_error
                break
        self.emit("tool_result", {"id": call_id, "name": name, "summary": summary})

    def _on_assistant_message(self, data: dict) -> None:
        text = _assistant_message_text(data)
        if text:
            self.emit("delta", {"text": text})


# --------------------------------------------------------------------------
# Session/message persistence helpers
# --------------------------------------------------------------------------


def _esc_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _scope_name(conn, scope: str) -> str:
    try:
        kind, sid_s = scope.split(":", 1)
        sid = int(sid_s)
    except ValueError:
        return ""
    table = "notebooks" if kind == "notebook" else "archives" if kind == "archive" else None
    if table is None:
        return ""
    row = db.one(conn, f"SELECT name FROM {table} WHERE id = ?", (sid,))
    return row["name"] if row else ""


def _context_prefix(scope: str, name: str, mode: str) -> str:
    return f'<study_context scope="{_esc_attr(scope)}" name="{_esc_attr(name)}" mode="{mode}"/>\n'


def _save_message(
    conn,
    session_id: str,
    role: str,
    content: str,
    citations: list[dict] | None = None,
    highlight: dict | None = None,
    tool_calls: list[dict] | None = None,
) -> dict:
    now = db.now_iso()
    cur = conn.execute(
        "INSERT INTO chat_messages"
        " (session_id, role, content, citations_json, highlight_json, tool_calls_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            role,
            content,
            json.dumps(citations or []),
            json.dumps(highlight or {}),
            json.dumps(tool_calls or []),
            now,
        ),
    )
    conn.execute("UPDATE chat_sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    return {
        "id": cur.lastrowid,
        "role": role,
        "content": content,
        "citations": citations or [],
        "highlight": highlight or {},
        "tool_calls": tool_calls or [],
        "created_at": now,
    }


def _maybe_set_title(conn, session_id: str, content: str) -> None:
    row = db.one(conn, "SELECT title FROM chat_sessions WHERE id = ?", (session_id,))
    if row is None or row["title"] != _DEFAULT_TITLE:
        return
    count = db.one(
        conn, "SELECT count(*) AS n FROM chat_messages WHERE session_id = ? AND role = 'user'", (session_id,)
    )
    if not count or count["n"] != 1:
        return
    title = " ".join(content.split())[:60] or _DEFAULT_TITLE
    conn.execute("UPDATE chat_sessions SET title = ? WHERE id = ?", (title, session_id))


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def run_turn(conn, session: dict, content: str, allow_general_knowledge: bool, emit: Callable[[str, dict], None]) -> None:
    """Run one chat turn end to end; synchronous and blocking.

    Saves the user message, emits `user_saved`, launches (or reuses) the dsh harness, streams
    `tool_call`/`tool_result`/`delta` events as the turn progresses, then strips unretrieved
    citation markers, saves the assistant message, and emits `done`. On any failure after the user
    message is saved, emits `error` and saves nothing else.
    """
    session_id = session["id"]
    scope = session["scope"]

    user_msg = _save_message(conn, session_id, "user", content)
    _maybe_set_title(conn, session_id, content)
    emit("user_saved", {"message_id": user_msg["id"]})

    settings = config.get_settings()
    if not settings.get("deepseek_api_key"):
        emit("error", {"message": "DeepSeek API key is not configured"})
        return

    manager = harness_module.get_manager()
    try:
        dsh = manager.get()
    except Exception as exc:
        log.exception("failed to start dsh harness")
        emit("error", {"message": str(exc)})
        return

    mode = "general" if allow_general_knowledge else "strict"
    name = _scope_name(conn, scope)
    prompt = _context_prefix(scope, name, mode) + content

    state = _TurnState(emit)
    try:
        with manager.turn_lock:
            result = dsh.run(prompt, session_id=session_id, on_notification=state.handle)
    except Exception as exc:
        log.exception("dsh turn failed")
        emit("error", {"message": str(exc)})
        return

    raw_text = result.final_response or ""
    clean_text, kept_markers = strip_and_collect_markers(
        raw_text, set(state.retrieved_chunk_ids), set(state.retrieved_block_ids)
    )
    citations = build_citations(conn, kept_markers)

    cited_chunk_ids = [num for kind, num in kept_markers if kind == "c"]
    other_chunk_ids = [cid for cid in state.retrieved_chunk_ids if cid not in cited_chunk_ids]
    highlight = _build_highlight(conn, scope, cited_chunk_ids + other_chunk_ids)

    assistant_msg = _save_message(
        conn, session_id, "assistant", clean_text, citations=citations, highlight=highlight, tool_calls=state.tool_calls
    )
    emit("done", {"message": assistant_msg})
