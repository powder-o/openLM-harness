"""Archives / notebooks / documents / blocks / chunks / settings / health endpoints (SPEC §13)."""
from __future__ import annotations

import json
import mimetypes
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config, db, llm
from ..graph import after_document_deleted
from ..index import embed
from ..ingest import web
from ..ingest.worker import enqueue

router = APIRouter(prefix="/api")

ALLOWED_EXT = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
}


def _remove_document_files(document_id: int) -> None:
    d = config.data_dir() / "files" / str(document_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- health / settings


@router.get("/health")
def health():
    llm_client = llm.get_llm()
    return {
        "ok": True,
        "llm_available": llm_client.available(),
        "embed_model": embed.model_name(),
        "fake_llm": config.fake_llm(),
        "fake_embed": config.fake_embed(),
    }


def _settings_dict() -> dict:
    s = config.get_settings()
    return {
        "deepseek_api_key_set": bool(s.get("deepseek_api_key")),
        "deepseek_base_url": s["deepseek_base_url"],
        "chat_model": s["chat_model"],
        "extract_model": s["extract_model"],
        "vision_model": s["vision_model"],
        "embed_model": s["embed_model"],
    }


@router.get("/settings")
def get_settings():
    return _settings_dict()


class SettingsPatch(BaseModel):
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    chat_model: str | None = None
    extract_model: str | None = None
    vision_model: str | None = None


@router.put("/settings")
def put_settings(body: SettingsPatch):
    config.save_settings(body.model_dump(exclude_none=True))
    return _settings_dict()


# ---------------------------------------------------------------- archives


def _archive_dict(conn, archive_id: int) -> dict:
    a = db.one(conn, "SELECT * FROM archives WHERE id=?", (archive_id,))
    nbs = db.rows(conn, "SELECT * FROM notebooks WHERE archive_id=? ORDER BY id", (archive_id,))
    nb_out = []
    for nb in nbs:
        cnt = db.one(conn, "SELECT COUNT(*) AS n FROM documents WHERE notebook_id=?", (nb["id"],))["n"]
        nb_out.append({"id": nb["id"], "name": nb["name"], "description": nb["description"], "document_count": cnt})
    return {
        "id": a["id"],
        "name": a["name"],
        "description": a["description"],
        "created_at": a["created_at"],
        "notebooks": nb_out,
    }


@router.get("/archives")
def list_archives(conn=Depends(db.get_conn)):
    ids = [r["id"] for r in db.rows(conn, "SELECT id FROM archives ORDER BY id")]
    return [_archive_dict(conn, i) for i in ids]


class ArchiveCreate(BaseModel):
    name: str
    description: str = ""


@router.post("/archives")
def create_archive(body: ArchiveCreate, conn=Depends(db.get_conn)):
    cur = conn.execute("INSERT INTO archives (name, description) VALUES (?, ?)", (body.name, body.description))
    return _archive_dict(conn, cur.lastrowid)


class ArchivePatch(BaseModel):
    name: str | None = None
    description: str | None = None


@router.patch("/archives/{archive_id}")
def patch_archive(archive_id: int, body: ArchivePatch, conn=Depends(db.get_conn)):
    if not db.one(conn, "SELECT id FROM archives WHERE id=?", (archive_id,)):
        raise HTTPException(404, "archive not found")
    if body.name is not None:
        conn.execute("UPDATE archives SET name=? WHERE id=?", (body.name, archive_id))
    if body.description is not None:
        conn.execute("UPDATE archives SET description=? WHERE id=?", (body.description, archive_id))
    return _archive_dict(conn, archive_id)


@router.delete("/archives/{archive_id}")
def delete_archive(archive_id: int, conn=Depends(db.get_conn)):
    if not db.one(conn, "SELECT id FROM archives WHERE id=?", (archive_id,)):
        raise HTTPException(404, "archive not found")
    doc_ids = [
        r["id"]
        for r in db.rows(
            conn,
            "SELECT d.id FROM documents d JOIN notebooks n ON n.id=d.notebook_id WHERE n.archive_id=?",
            (archive_id,),
        )
    ]
    conn.execute("DELETE FROM archives WHERE id=?", (archive_id,))
    for did in doc_ids:
        _remove_document_files(did)
    return {"ok": True}


# ---------------------------------------------------------------- notebooks


def _notebook_dict(conn, notebook_id: int) -> dict:
    nb = db.one(conn, "SELECT * FROM notebooks WHERE id=?", (notebook_id,))
    archive = db.one(conn, "SELECT name FROM archives WHERE id=?", (nb["archive_id"],))
    return {
        "id": nb["id"],
        "archive_id": nb["archive_id"],
        "archive_name": archive["name"] if archive else None,
        "name": nb["name"],
        "description": nb["description"],
        "settings": config.notebook_settings(nb["settings_json"]),
        "created_at": nb["created_at"],
    }


class NotebookCreate(BaseModel):
    name: str
    description: str = ""


@router.post("/archives/{archive_id}/notebooks")
def create_notebook(archive_id: int, body: NotebookCreate, conn=Depends(db.get_conn)):
    if not db.one(conn, "SELECT id FROM archives WHERE id=?", (archive_id,)):
        raise HTTPException(404, "archive not found")
    cur = conn.execute(
        "INSERT INTO notebooks (archive_id, name, description, settings_json) VALUES (?,?,?,?)",
        (archive_id, body.name, body.description, json.dumps(config.DEFAULT_NOTEBOOK_SETTINGS)),
    )
    return _notebook_dict(conn, cur.lastrowid)


@router.get("/notebooks/{notebook_id}")
def get_notebook(notebook_id: int, conn=Depends(db.get_conn)):
    if not db.one(conn, "SELECT id FROM notebooks WHERE id=?", (notebook_id,)):
        raise HTTPException(404, "notebook not found")
    return _notebook_dict(conn, notebook_id)


class NotebookPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    settings: dict | None = None


@router.patch("/notebooks/{notebook_id}")
def patch_notebook(notebook_id: int, body: NotebookPatch, conn=Depends(db.get_conn)):
    nb = db.one(conn, "SELECT * FROM notebooks WHERE id=?", (notebook_id,))
    if not nb:
        raise HTTPException(404, "notebook not found")
    if body.name is not None:
        conn.execute("UPDATE notebooks SET name=? WHERE id=?", (body.name, notebook_id))
    if body.description is not None:
        conn.execute("UPDATE notebooks SET description=? WHERE id=?", (body.description, notebook_id))
    if body.settings is not None:
        merged = config.notebook_settings(nb["settings_json"])
        merged.update(body.settings)
        conn.execute("UPDATE notebooks SET settings_json=? WHERE id=?", (json.dumps(merged), notebook_id))
    return _notebook_dict(conn, notebook_id)


@router.delete("/notebooks/{notebook_id}")
def delete_notebook(notebook_id: int, conn=Depends(db.get_conn)):
    if not db.one(conn, "SELECT id FROM notebooks WHERE id=?", (notebook_id,)):
        raise HTTPException(404, "notebook not found")
    doc_ids = [r["id"] for r in db.rows(conn, "SELECT id FROM documents WHERE notebook_id=?", (notebook_id,))]
    conn.execute("DELETE FROM notebooks WHERE id=?", (notebook_id,))
    for did in doc_ids:
        _remove_document_files(did)
    return {"ok": True}


# ---------------------------------------------------------------- documents


def _document_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "notebook_id": row["notebook_id"],
        "title": row["title"],
        "kind": row["kind"],
        "source_name": row["source_name"],
        "status": row["status"],
        "status_detail": row["status_detail"],
        "error": row["error"],
        "num_pages": row["num_pages"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("/notebooks/{notebook_id}/documents")
def list_documents(notebook_id: int, conn=Depends(db.get_conn)):
    if not db.one(conn, "SELECT id FROM notebooks WHERE id=?", (notebook_id,)):
        raise HTTPException(404, "notebook not found")
    return [_document_dict(r) for r in db.rows(conn, "SELECT * FROM documents WHERE notebook_id=? ORDER BY id", (notebook_id,))]


@router.post("/notebooks/{notebook_id}/documents")
async def upload_documents(notebook_id: int, files: list[UploadFile] = File(...), conn=Depends(db.get_conn)):
    if not db.one(conn, "SELECT id FROM notebooks WHERE id=?", (notebook_id,)):
        raise HTTPException(404, "notebook not found")
    out = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        kind = ALLOWED_EXT.get(ext)
        if kind is None:
            raise HTTPException(400, f"unsupported file type: {ext or '(none)'}")
        title = Path(f.filename).stem
        cur = conn.execute(
            "INSERT INTO documents (notebook_id, title, kind, source_name, status) VALUES (?,?,?,?, 'queued')",
            (notebook_id, title, kind, f.filename),
        )
        doc_id = cur.lastrowid
        dest_dir = config.document_dir(doc_id)
        dest = dest_dir / f"original{ext}"
        dest.write_bytes(await f.read())
        rel = dest.relative_to(config.data_dir())
        conn.execute("UPDATE documents SET file_path=? WHERE id=?", (str(rel), doc_id))
        out.append(_document_dict(db.one(conn, "SELECT * FROM documents WHERE id=?", (doc_id,))))
        enqueue(doc_id)
    return out


class UrlBody(BaseModel):
    url: str


@router.post("/notebooks/{notebook_id}/documents/url")
def upload_url(notebook_id: int, body: UrlBody, conn=Depends(db.get_conn)):
    if not db.one(conn, "SELECT id FROM notebooks WHERE id=?", (notebook_id,)):
        raise HTTPException(404, "notebook not found")
    cur = conn.execute(
        "INSERT INTO documents (notebook_id, title, kind, source_name, status) VALUES (?,?, 'web', ?, 'queued')",
        (notebook_id, body.url, body.url),
    )
    doc_id = cur.lastrowid
    dest_dir = config.document_dir(doc_id)
    try:
        path, title = web.fetch_and_save(body.url, dest_dir)
    except Exception as exc:
        conn.execute("UPDATE documents SET status='error', error=? WHERE id=?", (str(exc), doc_id))
        raise HTTPException(400, f"failed to fetch URL: {exc}")
    rel = path.relative_to(config.data_dir())
    conn.execute("UPDATE documents SET title=?, file_path=? WHERE id=?", (title, str(rel), doc_id))
    enqueue(doc_id)
    return _document_dict(db.one(conn, "SELECT * FROM documents WHERE id=?", (doc_id,)))


@router.get("/documents/{document_id}")
def get_document(document_id: int, conn=Depends(db.get_conn)):
    row = db.one(conn, "SELECT * FROM documents WHERE id=?", (document_id,))
    if not row:
        raise HTTPException(404, "document not found")
    page_sizes = json.loads(row["page_sizes_json"]) if row["page_sizes_json"] else None
    block_count = db.one(conn, "SELECT COUNT(*) AS n FROM blocks WHERE document_id=?", (document_id,))["n"]
    chunk_count = db.one(conn, "SELECT COUNT(*) AS n FROM chunks WHERE document_id=?", (document_id,))["n"]
    figure_count = db.one(
        conn, "SELECT COUNT(*) AS n FROM blocks WHERE document_id=? AND type IN ('figure','table')", (document_id,)
    )["n"]
    return {
        **_document_dict(row),
        "page_sizes": page_sizes,
        "block_count": block_count,
        "chunk_count": chunk_count,
        "figure_count": figure_count,
    }


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, conn=Depends(db.get_conn)):
    row = db.one(conn, "SELECT * FROM documents WHERE id=?", (document_id,))
    if not row:
        raise HTTPException(404, "document not found")
    notebook_id = row["notebook_id"]
    conn.execute("DELETE FROM documents WHERE id=?", (document_id,))
    _remove_document_files(document_id)
    after_document_deleted(conn, notebook_id)
    return {"ok": True}


@router.post("/documents/{document_id}/reingest")
def reingest_document(document_id: int, conn=Depends(db.get_conn)):
    if not db.one(conn, "SELECT id FROM documents WHERE id=?", (document_id,)):
        raise HTTPException(404, "document not found")
    conn.execute(
        "UPDATE documents SET status='queued', status_detail='', error=NULL, updated_at=? WHERE id=?",
        (db.now_iso(), document_id),
    )
    enqueue(document_id)
    return _document_dict(db.one(conn, "SELECT * FROM documents WHERE id=?", (document_id,)))


@router.get("/documents/{document_id}/file")
def get_document_file(document_id: int, conn=Depends(db.get_conn)):
    row = db.one(conn, "SELECT * FROM documents WHERE id=?", (document_id,))
    if not row or not row["file_path"]:
        raise HTTPException(404, "file not found")
    path = config.data_dir() / row["file_path"]
    if not path.exists():
        raise HTTPException(404, "file not found")
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


# ---------------------------------------------------------------- blocks / chunks


def _block_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "type": row["type"],
        "text": row["text"],
        "level": row["level"],
        "page": row["page"],
        "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
        "section_id": row["section_id"],
        "image_url": f"/api/blocks/{row['id']}/image" if row["image_path"] else None,
        "table_html": row["table_html"],
        "caption": row["caption"],
        "description": row["description"],
        "label": row["label"],
    }


@router.get("/documents/{document_id}/blocks")
def list_blocks(document_id: int, page: int | None = None, conn=Depends(db.get_conn)):
    if not db.one(conn, "SELECT id FROM documents WHERE id=?", (document_id,)):
        raise HTTPException(404, "document not found")
    if page is not None:
        rows_ = db.rows(conn, "SELECT * FROM blocks WHERE document_id=? AND page=? ORDER BY seq", (document_id, page))
    else:
        rows_ = db.rows(conn, "SELECT * FROM blocks WHERE document_id=? ORDER BY seq", (document_id,))
    return [_block_dict(r) for r in rows_]


@router.get("/blocks/{block_id}/image")
def get_block_image(block_id: int, conn=Depends(db.get_conn)):
    row = db.one(conn, "SELECT image_path FROM blocks WHERE id=?", (block_id,))
    if not row or not row["image_path"]:
        raise HTTPException(404, "image not found")
    path = config.data_dir() / row["image_path"]
    if not path.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(path, media_type="image/png")


@router.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: int, conn=Depends(db.get_conn)):
    c = db.one(
        conn,
        "SELECT c.*, d.title AS document_title, d.kind AS document_kind FROM chunks c"
        " JOIN documents d ON d.id=c.document_id WHERE c.id=?",
        (chunk_id,),
    )
    if not c:
        raise HTTPException(404, "chunk not found")
    block_ids = json.loads(c["block_ids_json"] or "[]")
    by_id = {}
    if block_ids:
        placeholders = ",".join("?" * len(block_ids))
        for b in db.rows(conn, f"SELECT id, page, bbox_json FROM blocks WHERE id IN ({placeholders})", block_ids):
            by_id[b["id"]] = b
    blocks_out = []
    for bid in block_ids:
        b = by_id.get(bid)
        if b is None:
            continue
        blocks_out.append({"id": b["id"], "page": b["page"], "bbox": json.loads(b["bbox_json"]) if b["bbox_json"] else None})

    figures_ = db.rows(
        conn,
        "SELECT b.id AS block_id, b.label, b.caption, b.image_path FROM figure_links fl"
        " JOIN blocks b ON b.id=fl.figure_block_id WHERE fl.chunk_id=?",
        (chunk_id,),
    )
    return {
        "id": c["id"],
        "document_id": c["document_id"],
        "document_title": c["document_title"],
        "document_kind": c["document_kind"],
        "kind": c["kind"],
        "text": c["text"],
        "heading_path": c["heading_path"],
        "page_start": c["page_start"],
        "page_end": c["page_end"],
        "blocks": blocks_out,
        "figures": [
            {
                "block_id": f["block_id"],
                "label": f["label"],
                "caption": f["caption"],
                "image_url": f"/api/blocks/{f['block_id']}/image" if f["image_path"] else None,
            }
            for f in figures_
        ],
    }
