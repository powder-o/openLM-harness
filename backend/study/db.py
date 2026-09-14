"""SQLite access. One connection per thread/request, autocommit, explicit `transaction()` for batches."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import config

_SCHEMA = Path(__file__).with_name("schema.sql")
_init_lock = threading.Lock()
_initialized: set[str] = set()


def init_db(path: Path | None = None) -> None:
    p = str(path or config.db_path())
    with _init_lock:
        if p in _initialized and Path(p).exists():
            return
        conn = sqlite3.connect(p)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA.read_text())
        finally:
            conn.close()
        _initialized.add(p)


def connect(path: Path | None = None) -> sqlite3.Connection:
    init_db(path)
    conn = sqlite3.connect(
        str(path or config.db_path()), timeout=30, check_same_thread=False, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Re-entrant: nested use joins the outer transaction."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def one(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def rows(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mark_graph_dirty(conn: sqlite3.Connection, notebook_id: int) -> None:
    nb = one(conn, "SELECT archive_id FROM notebooks WHERE id = ?", (notebook_id,))
    keys = [f"notebook:{notebook_id}"] + ([f"archive:{nb['archive_id']}"] if nb else [])
    for key in keys:
        conn.execute(
            "INSERT INTO graph_cache (key, dirty) VALUES (?, 1) ON CONFLICT(key) DO UPDATE SET dirty = 1",
            (key,),
        )
