"""End-to-end API smoke script (SPEC "Tasks" §2, integrator-owned).

NOT a pytest test module: pytest's default discovery only picks up `test_*.py` / `*_test.py`
files, so this file is skipped automatically (also listed in `collect_ignore` in conftest.py for
belt-and-suspenders). Run it directly:

    cd backend && uv run python tests/smoke_api.py

What it does: spins up a real `uvicorn` server in a subprocess against a fresh temp
`STUDY_DATA_DIR`, with `STUDY_LLM_FAKE=1` (offline, deterministic "LLM") but REAL docling parsing
and REAL local embeddings (bge-small-en-v1.5 — the first run on a machine downloads it, which can
take a few minutes). It generates fixtures, creates an archive with two notebooks, uploads
`two_column.pdf` + `notes.md` to notebook 1 and a small overlapping-topic markdown to notebook 2,
waits for ingestion, and then exercises the HTTP API end to end: document/block/chunk detail,
figure images, the original file download, search (notebook + archive scope), the notebook and
archive graphs (including cross-notebook concept merging), concept detail, all study features,
notes, chat status + the no-API-key error path, and static frontend serving.

Prints one PASS/FAIL line per check via `check()` and exits non-zero if anything failed.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[1]  # backend/
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tests.fixtures.make_fixtures import make_fixtures  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append((name, cond, detail))
    status = "PASS" if cond else "FAIL"
    line = f"[{status}] {name}"
    if detail and not cond:
        line += f" -- {detail}"
    print(line, flush=True)
    return cond


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http_ok(url: str, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code < 500:
                return True
        except Exception as exc:  # noqa: BLE001 - retry until timeout
            last_exc = exc
        time.sleep(0.3)
    print(f"server never became ready at {url}: {last_exc}", file=sys.stderr)
    return False


def _wait_document_ready(client: httpx.Client, doc_id: int, timeout: float = 600.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/documents/{doc_id}")
        r.raise_for_status()
        last = r.json()
        if last["status"] in ("ready", "error"):
            return last
        time.sleep(0.5)
    return last


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> None:
        if event_name is not None:
            payload = json.loads("\n".join(data_lines)) if data_lines else {}
            events.append((event_name, payload))

    for line in body.splitlines():
        if line == "":
            flush()
            event_name, data_lines = None, []
        elif line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    flush()
    return events


# --------------------------------------------------------------------------------------------
# Individual check groups. Each takes the httpx client + shared fixture ids and prints its own
# PASS/FAIL lines via `check()`; a failure in one group doesn't stop the others from running so a
# single run surfaces as many problems as possible.
# --------------------------------------------------------------------------------------------


def setup_archive_and_upload(client: httpx.Client, fixtures: dict[str, Path]) -> dict:
    r = client.post("/api/archives", json={"name": "Physics"})
    check("create archive", r.status_code == 200, r.text)
    archive_id = r.json()["id"]

    r = client.post(f"/api/archives/{archive_id}/notebooks", json={"name": "Thermo I"})
    check("create notebook 1", r.status_code == 200, r.text)
    nb1 = r.json()["id"]

    r = client.post(f"/api/archives/{archive_id}/notebooks", json={"name": "Thermo II"})
    check("create notebook 2", r.status_code == 200, r.text)
    nb2 = r.json()["id"]

    pdf_bytes = fixtures["two_column.pdf"].read_bytes()
    md_bytes = fixtures["notes.md"].read_bytes()
    r = client.post(
        f"/api/notebooks/{nb1}/documents",
        files=[
            ("files", ("two_column.pdf", pdf_bytes, "application/pdf")),
            ("files", ("notes.md", md_bytes, "text/markdown")),
        ],
    )
    ok = check("upload two_column.pdf + notes.md to notebook 1", r.status_code == 200, r.text)
    docs1 = r.json() if ok else []
    pdf_doc = next((d for d in docs1 if d["kind"] == "pdf"), None)
    md_doc = next((d for d in docs1 if d["kind"] == "md"), None)
    check("upload returned both documents", bool(pdf_doc and md_doc), str(docs1))

    # A small overlapping-topic markdown for notebook 2: shares the "Entropy" concept with
    # notebook 1's documents (both known-single-word matches for the fake concept extractor,
    # so they should merge into one archive concept), and separately states a "prerequisite"
    # sentence so /api/study/path has a real multi-step chain to walk.
    nb2_md = (
        "# More Thermodynamics Notes\n\n"
        "## Second Law\n\n"
        "Understanding the First Law of Thermodynamics is a prerequisite for the Second Law "
        "of Thermodynamics.\n\n"
        "## Entropy Revisited\n\n"
        "Entropy is also discussed here, and it never decreases in an isolated system.\n"
    )
    r = client.post(
        f"/api/notebooks/{nb2}/documents",
        files=[("files", ("overlap.md", nb2_md.encode(), "text/markdown"))],
    )
    ok = check("upload overlapping-topic markdown to notebook 2", r.status_code == 200, r.text)
    nb2_doc = r.json()[0] if ok else None

    return {
        "archive_id": archive_id, "nb1": nb1, "nb2": nb2,
        "pdf_doc_id": pdf_doc["id"] if pdf_doc else None,
        "md_doc_id": md_doc["id"] if md_doc else None,
        "nb2_doc_id": nb2_doc["id"] if nb2_doc else None,
    }


def wait_for_ingestion(client: httpx.Client, ids: dict) -> None:
    for label, key in (("two_column.pdf", "pdf_doc_id"), ("notes.md", "md_doc_id"), ("notebook 2 doc", "nb2_doc_id")):
        doc_id = ids.get(key)
        if doc_id is None:
            check(f"{label} ready", False, "document was never uploaded")
            continue
        doc = _wait_document_ready(client, doc_id)
        check(f"{label} reaches status ready", doc.get("status") == "ready", json.dumps(doc))


def check_document_and_blocks(client: httpx.Client, ids: dict) -> None:
    pdf_id = ids.get("pdf_doc_id")
    if pdf_id is None:
        return
    r = client.get(f"/api/documents/{pdf_id}")
    check("GET document detail", r.status_code == 200, r.text)
    detail = r.json()
    check("document detail has page_sizes for both pages", detail.get("page_sizes") == [] or len(detail.get("page_sizes") or []) == 2, json.dumps(detail))
    check("document detail has figure_count >= 1 (figure + table)", (detail.get("figure_count") or 0) >= 1, json.dumps(detail))
    check("document detail has block_count/chunk_count > 0", detail.get("block_count", 0) > 0 and detail.get("chunk_count", 0) > 0, json.dumps(detail))

    r = client.get(f"/api/documents/{pdf_id}/blocks")
    check("GET document blocks", r.status_code == 200, r.text)
    blocks = r.json()
    paged = [b for b in blocks if b["page"] is not None]
    check("blocks carry a page number", len(paged) > 0, "no paged blocks returned")
    with_bbox = [b for b in paged if b["type"] not in ("figure", "table") and b["bbox"] is not None]
    check("non-figure/table blocks carry a bbox", len(with_bbox) > 0, "no block had both page and bbox")

    fig_block = next((b for b in blocks if b["type"] == "figure" and b["image_url"]), None)
    check("a figure block with an image_url exists", fig_block is not None, str([b["type"] for b in blocks]))
    if fig_block is not None:
        img = client.get(fig_block["image_url"])
        ok = check("GET /api/blocks/{id}/image returns 200", img.status_code == 200, img.text)
        if ok:
            check(
                "block image is a PNG",
                img.headers.get("content-type", "").startswith("image/png") and img.content[:8] == b"\x89PNG\r\n\x1a\n",
                img.headers.get("content-type"),
            )

    fr = client.get(f"/api/documents/{pdf_id}/file")
    ok = check("GET /api/documents/{id}/file returns 200", fr.status_code == 200, fr.text)
    if ok:
        check(
            "document file is the original PDF",
            "pdf" in fr.headers.get("content-type", "") and fr.content[:4] == b"%PDF",
            fr.headers.get("content-type"),
        )


def check_chunks(client: httpx.Client, ids: dict) -> None:
    pdf_id = ids.get("pdf_doc_id")
    if pdf_id is None:
        return
    blocks = client.get(f"/api/documents/{pdf_id}/blocks").json()
    # any block belonging to a chunk works; walk chunks via a page-2 block that's part of a
    # figure-adjacent text chunk (page 2 holds the figure + table in the fixture PDF).
    r = client.get("/api/search", params={"scope": f"notebook:{ids['nb1']}", "q": "entropy figure vacuum"})
    check("GET /api/search (setup for chunk check)", r.status_code == 200, r.text)
    hits = r.json()
    if not hits:
        check("found a chunk to inspect via search", False, "no search hits")
        return
    chunk_id = hits[0]["chunk_id"]
    r = client.get(f"/api/chunks/{chunk_id}")
    ok = check("GET /api/chunks/{id}", r.status_code == 200, r.text)
    if ok:
        detail = r.json()
        check("chunk detail exposes document_title/kind", bool(detail.get("document_title")) and bool(detail.get("document_kind")), json.dumps(detail))
        boxed = [b for b in detail.get("blocks", []) if b.get("page") is not None and b.get("bbox") is not None]
        check("chunk's blocks carry page+bbox boxes", len(boxed) > 0, json.dumps(detail.get("blocks")))


def check_search(client: httpx.Client, ids: dict) -> None:
    for scope_label, scope in (("notebook", f"notebook:{ids['nb1']}"), ("archive", f"archive:{ids['archive_id']}")):
        r = client.get("/api/search", params={"scope": scope, "q": "Figure 1 entropy vacuum expansion", "k": 8})
        ok = check(f"search returns hits in {scope_label} scope", r.status_code == 200 and len(r.json()) > 0, r.text)
        if ok:
            hits = r.json()
            with_figures = [h for h in hits if h.get("figures")]
            check(f"{scope_label}-scope search hit includes linked figures", len(with_figures) > 0, json.dumps(hits))


def check_notebook_graph(client: httpx.Client, ids: dict) -> dict:
    r = client.get(f"/api/graph/notebook/{ids['nb1']}")
    ok = check("GET notebook graph", r.status_code == 200, r.text)
    if not ok:
        return {}
    payload = r.json()
    tiers = {n["tier"] for n in payload["nodes"]}
    check("notebook graph has structure+concept+topic tiers", {"structure", "concept", "topic"} <= tiers, str(tiers))
    node_ids = {n["id"] for n in payload["nodes"]}
    bad_edges = [e for e in payload["edges"] if e["source"] not in node_ids or e["target"] not in node_ids]
    check("all notebook graph edges reference existing nodes", not bad_edges, str(bad_edges[:5]))
    concept_node = next((n for n in payload["nodes"] if n["type"] == "concept" and n.get("ref", {}).get("concept_id")), None)
    check("notebook graph has at least one concept node with a concept_id ref", concept_node is not None, "")
    return {"concept_id": concept_node["ref"]["concept_id"] if concept_node else None}


def check_archive_graph(client: httpx.Client, ids: dict) -> None:
    r = client.post(f"/api/graph/archive/{ids['archive_id']}/rebuild")
    check("POST archive graph rebuild", r.status_code == 200, r.text)
    payload = r.json()
    acon_nodes = [n for n in payload["nodes"] if n["type"] == "concept" and n["id"].startswith("acon:")]
    check("archive graph has acon (archive concept) nodes", len(acon_nodes) > 0, json.dumps(payload.get("stats")))

    node_by_id = {n["id"]: n for n in payload["nodes"]}
    merged = False
    for acon in acon_nodes:
        mentioning_docs = [e["source"] for e in payload["edges"] if e["type"] == "mentions" and e["target"] == acon["id"]]
        notebooks = {node_by_id[d]["ref"]["notebook_id"] for d in mentioning_docs if d in node_by_id}
        if {ids["nb1"], ids["nb2"]} <= notebooks:
            merged = True
            break
    check("an archive concept is mentioned from documents in both notebooks (cross-book merge)", merged, json.dumps([n["label"] for n in acon_nodes]))


def check_concept_detail(client: httpx.Client, concept_id: int | None) -> None:
    if concept_id is None:
        check("GET /api/concepts/{id}", False, "no concept id available from notebook graph")
        return
    r = client.get(f"/api/concepts/{concept_id}")
    ok = check("GET /api/concepts/{id}", r.status_code == 200, r.text)
    if ok:
        detail = r.json()
        check("concept detail has name/kind/mentions/relations", all(k in detail for k in ("name", "kind", "mentions", "relations")), json.dumps(detail))


def check_study_features(client: httpx.Client, ids: dict) -> None:
    nb1 = ids["nb1"]
    r = client.post("/api/study/flashcards/generate", json={"notebook_id": nb1, "scope": {"kind": "notebook", "id": nb1}, "count": 5})
    ok = check("POST flashcards/generate", r.status_code == 200 and len(r.json()) > 0, r.text)
    if ok:
        card = r.json()[0]
        rr = client.post(f"/api/study/flashcards/{card['id']}/review", json={"grade": 1})
        check("POST flashcards/{id}/review", rr.status_code == 200 and rr.json()["reps"] == 1, rr.text)

    r = client.post("/api/study/quiz/generate", json={"notebook_id": nb1, "scope": {"kind": "notebook", "id": nb1}, "count": 3})
    ok = check("POST quiz/generate", r.status_code == 200 and len(r.json().get("questions", [])) > 0, r.text)
    if ok:
        quiz = r.json()
        answers = [q["answer_index"] for q in quiz["questions"]]
        rs = client.post(f"/api/study/quiz/{quiz['id']}/submit", json={"answers": answers})
        check("POST quiz/{id}/submit scores all-correct as 1.0", rs.status_code == 200 and rs.json()["score"] == 1.0, rs.text)

    r = client.post("/api/study/guides", json={"notebook_id": nb1, "scope": {"kind": "notebook", "id": nb1}})
    check("POST study/guides", r.status_code == 200 and bool(r.json().get("content_md")), r.text)
    lr = client.get("/api/study/guides", params={"notebook_id": nb1})
    check("GET study/guides lists the generated guide", lr.status_code == 200 and len(lr.json()) > 0, lr.text)

    mr = client.get("/api/study/mastery", params={"notebook_id": nb1})
    check("GET study/mastery", mr.status_code == 200 and isinstance(mr.json(), dict), mr.text)


def check_learning_path(client: httpx.Client, ids: dict) -> None:
    # "Second Law of Thermodynamics" was extracted from notebook 2's overlap.md, with a
    # prerequisite_of edge from "First Law of Thermodynamics" (see setup_archive_and_upload).
    r = client.get(f"/api/graph/notebook/{ids['nb2']}")
    ok = check("GET notebook 2 graph (for path setup)", r.status_code == 200, r.text)
    if not ok:
        return
    payload = r.json()
    second_law = next((n for n in payload["nodes"] if n["type"] == "concept" and "Second Law" in n["label"]), None)
    ok = check("notebook 2 graph has a 'Second Law' concept node", second_law is not None, str([n["label"] for n in payload["nodes"] if n["type"] == "concept"]))
    if not ok:
        return
    concept_id = second_law["ref"]["concept_id"]
    pr = client.get("/api/study/path", params={"concept_id": concept_id})
    ok = check("GET study/path", pr.status_code == 200, pr.text)
    if ok:
        path = pr.json()
        names = [s["name"] for s in path["steps"]]
        check("learning path orders First Law before Second Law, target last", "First Law of Thermodynamics" in names and names[-1] == second_law["label"], json.dumps(path))


def check_notes(client: httpx.Client, ids: dict) -> None:
    r = client.post(f"/api/notebooks/{ids['nb1']}/notes", json={"title": "Smoke note", "content_md": "# Smoke note\nA quick note about entropy."})
    ok = check("POST notebooks/{id}/notes", r.status_code == 201, r.text)
    if not ok:
        return
    doc = r.json()
    ready = _wait_document_ready(client, doc["id"])
    check("note document reaches status ready", ready.get("status") == "ready", json.dumps(ready))
    gr = client.get(f"/api/documents/{doc['id']}/note")
    check("GET documents/{id}/note round-trips content", gr.status_code == 200 and gr.json()["content_md"] == "# Smoke note\nA quick note about entropy.", gr.text)


def check_chat(client: httpx.Client, ids: dict) -> None:
    r = client.get("/api/chat/status")
    check("GET /api/chat/status", r.status_code == 200 and "ready" in r.json(), r.text)

    sr = client.post("/api/chat/sessions", json={"scope": f"notebook:{ids['nb1']}"})
    ok = check("POST /api/chat/sessions", sr.status_code == 200, sr.text)
    if not ok:
        return
    session_id = sr.json()["id"]
    mr = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "What is entropy?"})
    ok = check("POST chat message (no API key configured) returns 200 SSE stream", mr.status_code == 200, mr.text)
    if ok:
        events = _parse_sse(mr.text)
        names = [n for n, _ in events]
        check("chat SSE stream emits an error event when no API key is set", "error" in names, str(names))


def check_frontend_serving(client: httpx.Client) -> None:
    r = client.get("/")
    check("GET / serves the built frontend index.html", r.status_code == 200 and "<html" in r.text.lower(), r.text[:200])
    r2 = client.get("/notebook/1")
    check("GET /notebook/1 (unknown SPA route) also serves index.html", r2.status_code == 200 and "<html" in r2.text.lower(), r2.text[:200])
    r3 = client.get("/api/nonexistent")
    check("GET /api/nonexistent stays a 404 (not swallowed by the SPA fallback)", r3.status_code == 404, r3.text)


def run_checks(client: httpx.Client, fixtures: dict[str, Path]) -> None:
    ids = setup_archive_and_upload(client, fixtures)
    wait_for_ingestion(client, ids)
    check_document_and_blocks(client, ids)
    check_chunks(client, ids)
    check_search(client, ids)
    nb_info = check_notebook_graph(client, ids)
    check_archive_graph(client, ids)
    check_concept_detail(client, nb_info.get("concept_id"))
    check_study_features(client, ids)
    check_learning_path(client, ids)
    check_notes(client, ids)
    check_chat(client, ids)
    check_frontend_serving(client)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="study-smoke-") as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        fixtures_dir = Path(tmp) / "fixtures"
        port = _free_port()

        env = dict(os.environ)
        env["STUDY_DATA_DIR"] = str(data_dir)
        env["STUDY_PORT"] = str(port)
        env["STUDY_LLM_FAKE"] = "1"
        env.pop("STUDY_EMBED_FAKE", None)  # real embeddings per the integrator task

        print(f"Generating fixtures in {fixtures_dir} ...")
        fixtures = make_fixtures(fixtures_dir)

        print(f"Starting uvicorn on 127.0.0.1:{port} (data dir {data_dir}) ...")
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "study.app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(BACKEND_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log_lines: list[str] = []

        def _drain() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_lines.append(line)

        threading.Thread(target=_drain, daemon=True).start()

        base = f"http://127.0.0.1:{port}"
        try:
            server_ok = check("uvicorn subprocess starts and /api/health responds", _wait_http_ok(f"{base}/api/health", timeout=90))
            if server_ok:
                with httpx.Client(base_url=base, timeout=120) as client:
                    run_checks(client, fixtures)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            if any(not ok for _, ok, _ in RESULTS):
                print("\n--- server log tail ---")
                print("".join(log_lines[-80:]))

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{total} checks passed")
    failed = [n for n, ok, _ in RESULTS if not ok]
    if failed:
        print("FAILED:")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
