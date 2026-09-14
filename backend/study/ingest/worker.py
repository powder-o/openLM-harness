"""Background ingest worker: a single daemon thread fed by a queue (SPEC §6)."""
from __future__ import annotations

import logging
import queue
import threading
from contextlib import asynccontextmanager

from .. import db

log = logging.getLogger("study.ingest.worker")

_queue: "queue.Queue[int]" = queue.Queue()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _worker_loop() -> None:
    while True:
        document_id = _queue.get()
        try:
            from . import pipeline  # local import: avoid import cycles at module load time

            pipeline.run(document_id)
        except Exception:
            log.exception("unhandled error processing document %s", document_id)
        finally:
            _queue.task_done()


def _ensure_started() -> None:
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker_loop, name="ingest-worker", daemon=True)
            _thread.start()


def enqueue(document_id: int) -> None:
    """Queue a document for (re-)ingestion. Other implementers call this for notes, etc."""
    _ensure_started()
    _queue.put(document_id)


@asynccontextmanager
async def lifespan(app):
    _ensure_started()
    conn = db.connect()
    try:
        pending = db.rows(conn, "SELECT id FROM documents WHERE status NOT IN ('ready', 'error')")
    finally:
        conn.close()
    for row in pending:
        _queue.put(row["id"])
    yield
