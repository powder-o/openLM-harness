# Study App (POC)

A local study workspace. Create **archives**, put **notebooks** inside them, and upload books, PDFs,
Word files, web pages, Markdown, or your own notes. Each source is parsed layout-aware, indexed, and turned
into a knowledge graph. A grounded chat answers from your sources with clickable citations that open the
PDF at the exact spot, renders figures inline, and lights up the related graph nodes.

## How it fits together

```
frontend (Vite/React)  ──/api──►  backend (FastAPI, Python 3.12)
                                   ├─ ingest: docling (layout, OCR, tables, figures) → blocks with page+bbox
                                   ├─ index: chunks → SQLite FTS5 + local embeddings (bge-small)
                                   ├─ graph: structure tier · concept tier (DeepSeek extraction) · topics (Leiden)
                                   ├─ study: flashcards · quizzes · guides · learning paths · mastery
                                   └─ chat ──► DeepSeek Harness runtime (dsh, bundled with the Python SDK)
                                                 └─ calls study tools back over MCP at /mcp
```

- The chat agent loop, sessions, and DeepSeek model access come from the DeepSeek Harness
  (`deepseek-harness-sdk` on PyPI, pinned in `backend/pyproject.toml`); its `deepseek-harness-runtime-bin`
  dependency ships the `dsh` CLI as a standalone executable, so no Node or harness checkout is needed.
  A patch file gives it a study persona, disables shell/filesystem/web tools, and connects it to this
  backend's MCP tools. To run against a harness source build instead, set `DSH_BIN` to its
  `apps/cli/lib/bin.js`.
- Batch LLM work (concept extraction, figure descriptions, topic labels, study material) calls the
  DeepSeek API directly with the same key.
- Clustering is vendored from graphify (`backend/study/graph/cluster_graphify.py`).

## Run

Prerequisites: [`uv`](https://docs.astral.sh/uv/) and Node.js (only to build the frontend once).

```bash
./run.sh
```

`run.sh` runs `npm install && npm run build` in `frontend/` the first time (or with `REBUILD_FRONTEND=1`),
then `uv sync` in `backend/` — which installs everything else, including the chat runtime — and starts
the server. The runtime wheel is ~80 MB and exists for macOS (arm64, x86_64), Linux (x86_64, aarch64) and
Windows (x86_64).

Open http://127.0.0.1:8765, then add your DeepSeek API key in Settings (or export `DEEPSEEK_API_KEY`).
Without a key, ingestion, search, the structure graph, and the reader still work; chat, concept/topic tiers,
figure descriptions, and study generation need the key.

The first ingestion downloads docling's layout/OCR models and the embedding model, which takes a few minutes.

## Tests

```bash
cd backend
uv run pytest -m "not docling and not dsh"   # fast, offline (fake LLM + fake embeddings)
uv run pytest -m docling                     # real docling on generated PDFs (downloads models)
uv run pytest -m dsh                         # chat through the bundled dsh runtime + MCP against tests/mock_llm.py
uv run python tests/smoke_api.py             # end-to-end API smoke test: real server, real docling + embeddings
cd ../frontend && npm run build && npx vitest run
```

`tests/smoke_api.py` is a standalone script, not collected by `pytest`: it boots a real `uvicorn`
subprocess against a fresh temp data dir and drives the whole HTTP API (ingestion, search, graph,
study features, chat status/error path, static frontend serving). The first run downloads the
bge-small embedding model if it isn't already cached.

## Parser spike

Compare docling (the parser the app uses), a PyMuPDF baseline, and MinerU (if installed) on your own PDFs:

```bash
cd backend
uv run python spike/compare_parsers.py path/to/book.pdf path/to/paper.pdf --out spike/out
```

Each parser gets `blocks.json`, per-page overlay PNGs (boxes by type, numbered in reading order), and a
`summary.md` comparison.

## Layout

See `SPEC.md` for the data model, API, and module ownership. Runtime data lives in `data/`
(`study.db`, uploaded files, figure crops, harness home).
