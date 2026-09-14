"""Chat endpoints: sessions, messages, and the SSE turn stream (SPEC §11)."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlite3 import Connection
from starlette.responses import StreamingResponse

from .. import db
from ..agent import chat as chat_agent
from ..agent import harness

log = logging.getLogger("study.api.chat")

router = APIRouter(prefix="/api/chat", tags=["chat"])


class CreateSessionBody(BaseModel):
    scope: str
    title: str | None = None


class SendMessageBody(BaseModel):
    content: str
    allow_general_knowledge: bool = False


def _session_public(row: dict) -> dict:
    return {
        "id": row["id"],
        "scope": row["scope"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _message_public(row: dict) -> dict:
    # User messages are saved with no highlight (an empty `{}`), but the SPEC/frontend type is
    # `HighlightPayload | null` (an object always carrying `primary`/`related` arrays, or null) -
    # normalize the empty-object case to null so it matches that contract rather than a `{}` that
    # would violate the shape if anything ever read `.primary`/`.related` off it.
    highlight = json.loads(row["highlight_json"] or "{}") or None
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "citations": json.loads(row["citations_json"] or "[]"),
        "highlight": highlight,
        "tool_calls": json.loads(row["tool_calls_json"] or "[]"),
        "created_at": row["created_at"],
    }


@router.get("/status")
def get_status() -> dict:
    return harness.status()


@router.get("/sessions")
def list_sessions(scope: str | None = None, conn: Connection = Depends(db.get_conn)) -> list[dict]:
    if scope:
        rows = db.rows(conn, "SELECT * FROM chat_sessions WHERE scope = ? ORDER BY updated_at DESC", (scope,))
    else:
        rows = db.rows(conn, "SELECT * FROM chat_sessions ORDER BY updated_at DESC")
    return [_session_public(r) for r in rows]


@router.post("/sessions")
def create_session(body: CreateSessionBody, conn: Connection = Depends(db.get_conn)) -> dict:
    session_id = f"study-{uuid.uuid4().hex}"
    now = db.now_iso()
    conn.execute(
        "INSERT INTO chat_sessions (id, scope, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, body.scope, body.title or "New chat", now, now),
    )
    row = db.one(conn, "SELECT * FROM chat_sessions WHERE id = ?", (session_id,))
    return _session_public(row)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, conn: Connection = Depends(db.get_conn)) -> dict:
    conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: str, conn: Connection = Depends(db.get_conn)) -> list[dict]:
    rows = db.rows(conn, "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id", (session_id,))
    return [_message_public(r) for r in rows]


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: SendMessageBody, conn: Connection = Depends(db.get_conn)):
    session = db.one(conn, "SELECT * FROM chat_sessions WHERE id = ?", (session_id,))
    if session is None:
        raise HTTPException(404, "session not found")

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: str, data: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, data))

        def worker() -> None:
            worker_conn = db.connect()
            try:
                chat_agent.run_turn(worker_conn, session, body.content, body.allow_general_knowledge, emit)
            except Exception as exc:  # last-resort guard so the stream always terminates
                log.exception("unhandled chat turn failure")
                emit("error", {"message": str(exc)})
            finally:
                worker_conn.close()
                emit("__end__", {})

        future = loop.run_in_executor(None, worker)
        try:
            while True:
                event, data = await queue.get()
                if event == "__end__":
                    break
                yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
        finally:
            try:
                await future
            except Exception:
                log.exception("chat worker thread raised after streaming ended")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
