# Study App — POC spec (source of truth for all implementers)

A local, single-user study tool. Archives contain notebooks; notebooks contain uploaded sources
(PDF, DOCX, HTML, web URL, Markdown, TXT, user notes). Sources are parsed layout-aware, chunked with
page+bbox provenance, indexed (FTS5 + local embeddings), turned into a three-tier knowledge graph
(structure / concepts / topics), and queried through a grounded chat agent that runs on the
DeepSeek Harness (`dsh`) via its Python SDK, with study tools exposed to the agent over MCP.

**Rule for implementers: do not over-engineer.** POC quality, working end to end, small readable modules.
Only touch files you own (see "Ownership"). If a contract here is wrong, fix it minimally and say so in
your final report.

## 1. Layout

```
study-app/
  SPEC.md  README.md  run.sh
  backend/                      Python 3.12, uv project, package `study`
    pyproject.toml
    study/
      config.py                 paths + settings            (foundation, done)
      db.py  schema.sql         sqlite connection + schema  (foundation, done)
      llm.py                    DeepSeek client + FakeLLM   (foundation, done)
      index/embed.py            embeddings (+ fake mode)    (foundation, done)
      app.py                    FastAPI app, mounts routers (foundation, done)
      api/library.py            archives/notebooks/documents/blocks/chunks/settings   [A]
      api/search.py             search endpoint                                       [A]
      ingest/worker.py          background job thread                                 [A]
      ingest/pipeline.py        stages orchestration                                  [A]
      ingest/parse_docling.py   PDF/DOCX/HTML -> canonical blocks                     [A]
      ingest/parse_text.py      md/txt -> canonical blocks                            [A]
      ingest/web.py             URL -> saved HTML -> docling                          [A]
      ingest/chunk.py           blocks -> chunks                                      [A]
      ingest/figures.py         figure crops, descriptions, figure<->text links       [A]
      index/search.py           hybrid search                                         [A]
      graph/__init__.py         ingest hooks (stub now)                               [G]
      graph/structure.py concepts.py merge.py topics.py layout.py build.py highlight.py [G]
      graph/cluster_graphify.py vendored from ../graphify-8/graphify/cluster.py        [G]
      api/graph.py              graph + concept endpoints                             [G]
      study_tools/*.py          flashcards, quiz, guide, path, mastery                [G]
      api/study.py              study endpoints (+ notes endpoints)                   [G]
      agent/study.patch.template.yml   dsh patch                                      [C]
      agent/mcp_server.py       MCP tools                                             [C]
      agent/harness.py          dsh runtime manager                                   [C]
      agent/chat.py             turn orchestration, citations                         [C]
      api/chat.py               chat endpoints + SSE                                  [C]
    tests/
      conftest.py               tmp data dir, fake LLM/embeddings, sample notebook (foundation, done)
      fixtures/make_fixtures.py generates PDFs/MD for tests                            [A]
      test_ingest.py test_search.py                                                   [A]
      test_graph.py test_study.py                                                     [G]
      test_chat_mock.py                                                               [C]
      smoke_api.py              end-to-end API smoke script                           [I]
    spike/compare_parsers.py    parser comparison spike                               [A]
  frontend/                     Vite + React 18 + TS                                  [F]
  data/                         runtime data (gitignored)
```
Owners: A = ingest/index, G = graph/study, C = chat/agent, F = frontend, I = integrator.

## 2. Runtime config (`study/config.py`, already written)

Env vars (all optional): `STUDY_DATA_DIR` (default `study-app/data`), `STUDY_PORT` (8765),
`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL` (https://api.deepseek.com), `STUDY_CHAT_MODEL` (deepseek-v4-flash),
`STUDY_EXTRACT_MODEL` (deepseek-v4-flash), `STUDY_VISION_MODEL` (deepseek-v4-flash-vision-exp),
`STUDY_EMBED_MODEL` (BAAI/bge-small-en-v1.5), `STUDY_EMBED_FAKE=1`, `STUDY_LLM_FAKE=1`,
`DSH_BIN` (optional: a dsh CLI to launch instead of the runtime bundled with `deepseek-harness-sdk`).
Settings saved from the UI live in `data/settings.json` and override env. `config.get_settings()` returns
the merged view; `config.save_settings(patch)` writes.

Data dir: `study.db`, `files/{document_id}/original.<ext>`, `files/{document_id}/figures/{block_id}.png`,
`dsh-home/`, `dsh-workspace/`.

## 3. Database (`study/schema.sql`, already written)

Read `backend/study/schema.sql` — it is the contract. Key conventions:
- `blocks.bbox_json` = `[x0, y0, x1, y1]` **normalized 0..1, top-left origin**, or NULL for non-paged docs.
- `blocks.page` is **1-based**, NULL for non-paged docs (md, txt, html, web, docx).
- `blocks.section_id` = id of the nearest preceding heading block (NULL before the first heading).
- `chunks.block_ids_json` = JSON list of block ids in reading order. `chunks.heading_path` = "Doc > H1 > H2".
- `chunks.embedding` / `concepts.embedding` = float32 little-endian bytes of an L2-normalized vector
  (`embed.to_blob` / `embed.from_blob`).
- `chunks.kind` = `text | figure | table`. Figures and tables get their own chunk.
- `chunks_fts` is an external-content FTS5 table kept in sync by triggers.
- `graph_cache` rows are keyed `notebook:{id}` / `archive:{id}`; set `dirty=1` when content changes.
- Use `db.connect()` per thread/request (WAL mode). FastAPI dependency: `db.get_conn`.

## 4. Canonical block types

`heading | paragraph | list_item | caption | figure | table | equation | code | footnote`
(page headers/footers/page numbers are dropped). Figure blocks: `image_path`, `caption`, `label`
(e.g. "Figure 3.2" parsed from caption), `description` (vision model). Table blocks: `table_html`,
`caption`, `label`, `text` (plain-text rendering of the table).

## 5. Notebook settings (JSON in `notebooks.settings_json`, defaults in `config.DEFAULT_NOTEBOOK_SETTINGS`)

```json
{"ocr": "auto", "ocr_lang": ["en"], "table_structure": true, "chunk_max_tokens": 350,
 "chunk_overlap_tokens": 40, "describe_figures": true, "extract_concepts": true, "graph_section_depth": 2}
```
`ocr`: auto (OCR only pages without a text layer) | force | off.

## 6. Ingestion pipeline [A]

Document status values, in order: `queued → parsing → describing → indexing → graphing → ready` (or `error`).
`status_detail` is a short human string ("page 12/40", "3/9 figures").
1. parse → blocks (docling for pdf/docx/html/web; parse_text for md/txt/note).
2. figures → crop images (docling picture images, or render bbox region with PyMuPDF), labels from caption,
   descriptions via `llm.describe_image` when `describe_figures` and LLM available (skip silently otherwise;
   fall back description = caption).
3. chunk (setting-driven; never cross a heading of level ≤ 2; figure/table blocks become their own chunk
   whose text = label + caption + description / table text).
4. embed chunks, FTS via triggers.
5. figure links: `explicit_mention` (regex "Fig./Figure/Table N[.N]" matches label, score 1.0),
   `caption_adjacent` (nearest text chunks before/after on same page, 0.6), `semantic` (top-2 text chunks in
   same document with cosine ≥ 0.6).
6. status `graphing` → call `study.graph.after_document_indexed(conn, document_id, progress)` where
   `progress(detail: str)` updates status_detail. Then `ready`.
Deleting a document removes its rows/files and calls `study.graph.after_document_deleted(conn, notebook_id)`.
The worker is a single daemon thread fed by a queue; on startup it re-queues documents not `ready`/`error`.

## 7. Search [A] — `study/index/search.py`

```python
def parse_scope(scope: str) -> tuple[str, int]           # "notebook:1" -> ("notebook", 1)
def scope_notebook_ids(conn, kind: str, id: int) -> list[int]
def hybrid_search(conn, scope: str, query: str, k: int = 8) -> list[dict]
# hit: {chunk_id, document_id, document_title, notebook_id, kind, page_start, page_end,
#       heading_path, text, score, figures: [{block_id, label, caption}]}
def chunk_in_scope(conn, scope: str, chunk_id: int) -> bool
```
FTS5 bm25 top-30 + cosine top-30 (numpy over the scope's chunk embeddings) fused with RRF (k=60).

## 8. Graph [G]

Hooks in `study/graph/__init__.py`: `after_document_indexed(conn, document_id, progress)` (extract concepts
if LLM available and `extract_concepts`, merge into notebook concepts, mark caches dirty) and
`after_document_deleted(conn, notebook_id)`.

Tiers:
- **Structure** (deterministic): document, sections (headings with level ≤ `graph_section_depth`, deeper
  headings roll up), figures, tables. Edges `contains`.
- **Concepts** (LLM, graphify-style, see §10 prompts): concept kinds
  `concept|definition|theorem|method|person|term|formula|event`; relations
  `prerequisite_of|part_of|example_of|contrasts_with|causes|uses|related_to` with confidence
  `EXTRACTED|INFERRED|AMBIGUOUS` and evidence. Mentions link concepts to chunks. Section→concept
  `mentions` edges (top 3 sections per concept). Concept→figure `illustrated_by` when a mentioning chunk has
  a figure link.
- **Topics**: Leiden (vendored graphify `cluster()`, Louvain fallback) over the concept graph (relation edges
  + co-mention edges), labelled by LLM (`TASK: topic_label`) or by hub concept name when no LLM.
- **Merging**: within a notebook, a new concept merges into an existing one when norm_name equal, or rapidfuzz
  token_sort_ratio ≥ 92, or embedding cosine ≥ 0.9 (same notebook). Across an archive, notebook concepts are
  merged the same way into `archive_concepts`, topics recomputed at archive level.
- Layout: networkx spring layout (seed 42) computed server-side; cached in `graph_cache`.

Payload (`GET /api/graph/notebook/{id}`, `GET /api/graph/archive/{id}`; `POST .../rebuild`):
```json
{"scope": {"kind": "notebook", "id": 1}, "built_at": "iso",
 "nodes": [{"id": "con:4", "type": "concept", "label": "Entropy", "x": 0.1, "y": -0.3, "size": 8,
            "tier": "structure|concept|topic", "kind": "definition", "topic": "topic:2", "mastery": 0.4,
            "ref": {"document_id": 3, "block_id": 10, "concept_id": 4, "notebook_id": 1, "page": 2}}],
 "edges": [{"id": "e1", "source": "sec:10", "target": "con:4", "type": "mentions", "confidence": "EXTRACTED"}],
 "stats": {"documents": 2, "sections": 30, "concepts": 120, "topics": 9}}
```
Node ids: `nb:{notebook_id}`, `doc:{document_id}`, `sec:{heading_block_id}`, `fig:{block_id}`,
`tab:{block_id}`, `con:{concept_id}`, `acon:{archive_concept_id}`, `topic:{topic_id}`.
Node types: `notebook|document|section|figure|table|concept|topic`. Archive payload uses `nb`, `doc`,
`acon`, `topic` nodes (no sections), edges `contains`, `mentions` (doc→acon), relations, `member_of`.

Highlight (`study/graph/highlight.py`):
```python
def highlight_for_chunks(conn, scope: str, chunk_ids: list[int]) -> dict  # {"primary": [...], "related": [...]}
```
primary = doc, section (rolled up to depth), figure/table and concept (or `acon`) nodes for those chunks;
related = 1-hop concept neighbours of primary concepts and their topic nodes.

Other endpoints: `GET /api/concepts/{id}` →
`{id, name, kind, summary, topic: {id, label} | null, mastery, mentions: [{chunk_id, evidence, document_id,
document_title, page_start}], relations: [{relation, direction: "out"|"in", confidence, other: {id, name}}]}`.

## 9. Study features [G] — `api/study.py`

Scope object: `{"kind": "notebook|document|section|concept", "id": N}` plus `notebook_id` in the body.
- `POST /api/study/flashcards/generate {notebook_id, scope, count=10}` → `[card]`
- `GET /api/study/flashcards?notebook_id=&due_only=false` → `[card]`
  card: `{id, notebook_id, concept_id, chunk_id, front, back, due_at, reps, interval_days, ease}`
- `POST /api/study/flashcards/{id}/review {grade: 0|1|2}` (again/good/easy, SM-2-lite) → card
- `DELETE /api/study/flashcards/{id}`
- `POST /api/study/quiz/generate {notebook_id, scope, count=5}` →
  `{id, questions: [{id, question, options: [4 strings], answer_index, explanation, concept_id, chunk_id}]}`
- `POST /api/study/quiz/{id}/submit {answers: [int]}` → `{score, results: [{question_id, correct, answer_index, explanation, chunk_id}]}`
- `POST /api/study/guides {notebook_id, scope}` → `{id, title, content_md, created_at}` (content cites `[c:N]`)
- `GET /api/study/guides?notebook_id=` → `[guide]`
- `GET /api/study/path?concept_id=` → `{target: {id, name}, steps: [{concept_id, name, summary, mastery, node_id}]}`
  (prerequisite ancestors, topologically ordered, cycle-safe, target last)
- `GET /api/study/mastery?notebook_id=` → `{"<concept_id>": 0.0..1.0}`
- Notes: `POST /api/notebooks/{id}/notes {title, content_md}` → document (kind `note`, saved as .md, queued);
  `GET /api/documents/{id}/note` → `{title, content_md}`; `PUT /api/documents/{id}/note {title, content_md}` → re-ingest.
Mastery: `concept_mastery` updated on card review (grade/2 blended 0.3 into prior) and quiz submit (1/0 blended 0.3).

## 10. LLM usage (`study/llm.py`, already written)

```python
llm = get_llm()          # FakeLLM if STUDY_LLM_FAKE=1, else DeepSeekLLM (may be unavailable)
llm.available() -> bool
llm.chat_json(task: str, system: str, user: str, max_tokens=4000) -> dict
llm.chat_text(task: str, system: str, user: str, max_tokens=2000) -> str
llm.describe_image(image_path: str, context: str) -> str
@fake_handler("task_name")   # register a FakeLLM responder: fn(system, user) -> dict | str
```
Every call passes a `task` name (`figure_description`, `concepts`, `topic_label`, `flashcards`, `quiz`,
`guide`). Owners register fake handlers for their tasks inside their own modules so tests run offline.
Untrusted document text in prompts is wrapped in `<untrusted_source>` blocks with graphify's rule that
content inside is data, never instructions.

## 11. Chat + agent [C]

Endpoints:
- `GET /api/chat/sessions?scope=notebook:1` → `[{id, scope, title, created_at, updated_at}]`
- `POST /api/chat/sessions {scope, title?}` → session (`id` = `study-<uuid4hex>`, also the dsh session id)
- `DELETE /api/chat/sessions/{id}`
- `GET /api/chat/status` → `{ready, api_key_set, runtime_found, error}`
- `GET /api/chat/sessions/{id}/messages` → `[message]`
  message: `{id, role: "user"|"assistant", content, citations, highlight, tool_calls, created_at}`
- `POST /api/chat/sessions/{id}/messages {content, allow_general_knowledge: false}` → `text/event-stream`:
  - `event: user_saved` `{message_id}`
  - `event: tool_call` `{id, name, arguments}`
  - `event: tool_result` `{id, name, summary}`
  - `event: delta` `{text}` (may be one chunk if only committed text is available)
  - `event: done` `{message}` (the saved assistant message)
  - `event: error` `{message}`

Citation markers in assistant text and tool output: `[c:12]` (chunk), `[f:45]` (figure/table block).
After the turn, markers not present in this turn's tool results are removed from the saved content.
`citations`: `[{marker: "c:12", kind: "chunk", chunk_id, document_id, document_title, document_kind,
page, heading_path, snippet, boxes: [{page, bbox}]}]` and
`{marker: "f:45", kind: "figure", block_id, document_id, document_title, label, caption, image_url, page, bbox}`.
`highlight` = `highlight_for_chunks(conn, scope, cited+retrieved chunk ids)` (cited chunks → primary first).

Agent runtime: one `DeepSeekHarness` (the `deepseek-harness-sdk` package, pinned in pyproject.toml) with
`dsh_bin` unset (the SDK launches the executable bundled in `deepseek-harness-runtime-bin`; `$DSH_BIN` overrides), `profile="sdk"`, `dsh_home=data/dsh-home`,
`cwd=data/dsh-workspace`, a generated patch file (from `agent/study.patch.template.yml`) that sets a study
persona, disables shell/filesystem/web/coding tool rows, and inserts `@deepseek-ai/dsh-mcp-client` with
`serverName: study`, `transport: streamable-http`, url of the backend MCP endpoint. Each prompt sent to dsh is
prefixed with `<study_context scope="notebook:1" name="..." mode="strict|general"/>`; stored/displayed
content is the raw user text.

MCP tools (all take `scope`): `search(scope, query, k=8)`, `read_context(scope, chunk_id, window=1)`,
`get_figure(scope, block_id)`, `concept_lookup(scope, name)`, `list_documents(scope)`. Tool output lists
chunks as `[c:ID] Title · p.X · heading path` followed by text, and linked figures as `[f:ID] label — caption`.

## 12. Frontend [F]

Vite + React 18 + TypeScript, react-router-dom, sigma + graphology, pdfjs-dist, react-markdown + remark-gfm.
Plain CSS. Dev proxy `/api` → `http://127.0.0.1:8765`. `npm run build` → `frontend/dist` (served by backend).
Screens: sidebar (archives → notebooks, create/rename/delete, settings dialog for API key/models);
`/notebook/:id` = chat (center) + right panel tabs Graph | Sources | Reader | Study;
`/archive/:id` = archive graph + archive-scoped chat. Details in the frontend task.

## 13. Library endpoints [A]

- `GET /api/health` → `{ok, llm_available, embed_model, fake_llm, fake_embed}`
- `GET /api/settings` → `{deepseek_api_key_set, deepseek_base_url, chat_model, extract_model, vision_model, embed_model}`;
  `PUT /api/settings {deepseek_api_key?, deepseek_base_url?, chat_model?, extract_model?, vision_model?}`
- `GET /api/archives` → `[{id, name, description, created_at, notebooks: [{id, name, description, document_count}]}]`
- `POST /api/archives {name, description?}` · `PATCH /api/archives/{id}` · `DELETE /api/archives/{id}`
- `POST /api/archives/{id}/notebooks {name, description?}` → notebook
- `GET /api/notebooks/{id}` → `{id, archive_id, archive_name, name, description, settings, created_at}`
- `PATCH /api/notebooks/{id} {name?, description?, settings?}` · `DELETE /api/notebooks/{id}`
- `GET /api/notebooks/{id}/documents` → `[document]`
  document: `{id, notebook_id, title, kind, source_name, status, status_detail, error, num_pages, created_at, updated_at}`
- `POST /api/notebooks/{id}/documents` multipart field `files` (1..n) → `[document]`
- `POST /api/notebooks/{id}/documents/url {url}` → document
- `GET /api/documents/{id}` → document + `{page_sizes: [[w, h]], block_count, chunk_count, figure_count}`
- `DELETE /api/documents/{id}` · `POST /api/documents/{id}/reingest`
- `GET /api/documents/{id}/file` → original bytes (correct content type)
- `GET /api/documents/{id}/blocks?page=N` → `[block]`
  block: `{id, seq, type, text, level, page, bbox, section_id, image_url, table_html, caption, description, label}`
- `GET /api/blocks/{id}/image` → png
- `GET /api/chunks/{id}` → `{id, document_id, document_title, document_kind, kind, text, heading_path,
  page_start, page_end, blocks: [{id, page, bbox}], figures: [{block_id, label, caption, image_url}]}`
- `GET /api/search?scope=notebook:1&q=...&k=8` → `[hit]`
Errors: HTTP 4xx/5xx with `{"detail": "..."}`.
