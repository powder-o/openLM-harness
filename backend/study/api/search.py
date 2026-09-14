"""GET /api/search (SPEC §13, §7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..index import search as search_mod

router = APIRouter(prefix="/api")


@router.get("/search")
def search(scope: str, q: str, k: int = 8, conn=Depends(db.get_conn)):
    try:
        return search_mod.hybrid_search(conn, scope, q, k)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
