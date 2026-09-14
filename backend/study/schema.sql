PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS archives (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS notebooks (
  id INTEGER PRIMARY KEY,
  archive_id INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  settings_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  notebook_id INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,                       -- pdf | docx | html | web | md | txt | note
  source_name TEXT NOT NULL DEFAULT '',     -- original filename or URL
  file_path TEXT,                           -- relative to data dir
  num_pages INTEGER,
  page_sizes_json TEXT,                     -- [[w, h], ...] in PDF points, index 0 = page 1
  status TEXT NOT NULL DEFAULT 'queued',    -- queued|parsing|describing|indexing|graphing|ready|error
  status_detail TEXT NOT NULL DEFAULT '',
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS documents_nb ON documents(notebook_id);

CREATE TABLE IF NOT EXISTS blocks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,                     -- reading order
  type TEXT NOT NULL,                       -- heading|paragraph|list_item|caption|figure|table|equation|code|footnote
  text TEXT NOT NULL DEFAULT '',
  level INTEGER,                            -- heading level (1 = top)
  page INTEGER,                             -- 1-based; NULL for non-paged docs
  bbox_json TEXT,                           -- [x0, y0, x1, y1] normalized 0..1, top-left origin
  section_id INTEGER,                       -- nearest preceding heading block id
  image_path TEXT,                          -- relative to data dir (figures, tables)
  table_html TEXT,
  caption TEXT,
  description TEXT,
  label TEXT                                -- "Figure 3.2", "Table 1"
);
CREATE INDEX IF NOT EXISTS blocks_doc_seq ON blocks(document_id, seq);
CREATE INDEX IF NOT EXISTS blocks_doc_page ON blocks(document_id, page);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  notebook_id INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'text',        -- text | figure | table
  text TEXT NOT NULL,
  heading_path TEXT NOT NULL DEFAULT '',
  block_ids_json TEXT NOT NULL DEFAULT '[]',
  section_id INTEGER,
  page_start INTEGER,
  page_end INTEGER,
  token_count INTEGER NOT NULL DEFAULT 0,
  embedding BLOB                            -- float32 LE, L2-normalized
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id, seq);
CREATE INDEX IF NOT EXISTS chunks_nb ON chunks(notebook_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, heading_path, content='chunks', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text, heading_path) VALUES (new.id, new.text, new.heading_path);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text, heading_path) VALUES ('delete', old.id, old.text, old.heading_path);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE OF text, heading_path ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text, heading_path) VALUES ('delete', old.id, old.text, old.heading_path);
  INSERT INTO chunks_fts(rowid, text, heading_path) VALUES (new.id, new.text, new.heading_path);
END;

CREATE TABLE IF NOT EXISTS figure_links (
  id INTEGER PRIMARY KEY,
  figure_block_id INTEGER NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
  chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  method TEXT NOT NULL,                     -- explicit_mention | caption_adjacent | semantic
  score REAL NOT NULL DEFAULT 1.0,
  UNIQUE (figure_block_id, chunk_id, method)
);
CREATE INDEX IF NOT EXISTS figure_links_chunk ON figure_links(chunk_id);

CREATE TABLE IF NOT EXISTS concepts (
  id INTEGER PRIMARY KEY,
  notebook_id INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  norm_name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'concept',
  summary TEXT NOT NULL DEFAULT '',
  aliases_json TEXT NOT NULL DEFAULT '[]',
  embedding BLOB,
  topic_id INTEGER
);
CREATE INDEX IF NOT EXISTS concepts_nb_norm ON concepts(notebook_id, norm_name);

CREATE TABLE IF NOT EXISTS concept_mentions (
  concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  evidence TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (concept_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS concept_mentions_chunk ON concept_mentions(chunk_id);

CREATE TABLE IF NOT EXISTS concept_edges (
  id INTEGER PRIMARY KEY,
  notebook_id INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  source_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  target_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,                   -- prerequisite_of|part_of|example_of|contrasts_with|causes|uses|related_to
  confidence TEXT NOT NULL DEFAULT 'EXTRACTED',
  evidence TEXT NOT NULL DEFAULT '',
  chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
  UNIQUE (source_id, target_id, relation)
);

CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY,
  notebook_id INTEGER REFERENCES notebooks(id) ON DELETE CASCADE,
  archive_id INTEGER REFERENCES archives(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  member_ids_json TEXT NOT NULL DEFAULT '[]'  -- concept ids (notebook) or archive_concept ids (archive)
);

CREATE TABLE IF NOT EXISTS archive_concepts (
  id INTEGER PRIMARY KEY,
  archive_id INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  norm_name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'concept',
  summary TEXT NOT NULL DEFAULT '',
  member_concept_ids_json TEXT NOT NULL DEFAULT '[]',
  topic_id INTEGER
);

CREATE TABLE IF NOT EXISTS concept_mastery (
  concept_id INTEGER PRIMARY KEY REFERENCES concepts(id) ON DELETE CASCADE,
  score REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS flashcards (
  id INTEGER PRIMARY KEY,
  notebook_id INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  concept_id INTEGER REFERENCES concepts(id) ON DELETE SET NULL,
  chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
  front TEXT NOT NULL,
  back TEXT NOT NULL,
  ease REAL NOT NULL DEFAULT 2.5,
  interval_days REAL NOT NULL DEFAULT 0,
  reps INTEGER NOT NULL DEFAULT 0,
  due_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  last_grade INTEGER,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS quizzes (
  id INTEGER PRIMARY KEY,
  notebook_id INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  scope_json TEXT NOT NULL DEFAULT '{}',
  questions_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
  id INTEGER PRIMARY KEY,
  quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
  answers_json TEXT NOT NULL DEFAULT '[]',
  score REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS guides (
  id INTEGER PRIMARY KEY,
  notebook_id INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  scope_json TEXT NOT NULL DEFAULT '{}',
  title TEXT NOT NULL,
  content_md TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS chat_sessions (
  id TEXT PRIMARY KEY,                      -- also the dsh session id
  scope TEXT NOT NULL,                      -- "notebook:1" | "archive:2"
  title TEXT NOT NULL DEFAULT 'New chat',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,                       -- user | assistant
  content TEXT NOT NULL,
  citations_json TEXT NOT NULL DEFAULT '[]',
  highlight_json TEXT NOT NULL DEFAULT '{}',
  tool_calls_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS graph_cache (
  key TEXT PRIMARY KEY,                     -- "notebook:1" | "archive:2"
  payload_json TEXT,
  built_at TEXT,
  dirty INTEGER NOT NULL DEFAULT 1
);
