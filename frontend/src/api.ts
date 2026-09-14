// Typed fetch functions + types mirroring SPEC.md. The backend runs at
// http://127.0.0.1:8765 and is proxied at /api during dev (see vite.config.ts);
// in production the backend serves frontend/dist directly.

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData;
  const headers: Record<string, string> = isForm
    ? {}
    : { "Content-Type": "application/json" };
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
  });
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && typeof data.detail === "string") detail = data.detail;
    } catch {
      // body wasn't JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

function withBody(method: string, body?: unknown): RequestInit {
  return { method, body: body === undefined ? undefined : JSON.stringify(body) };
}

// ---------------------------------------------------------------------------
// Shared / library types (SPEC §13)

export interface HealthStatus {
  ok: boolean;
  llm_available: boolean;
  embed_model: string;
  fake_llm: boolean;
  fake_embed: boolean;
}

export interface Settings {
  deepseek_api_key_set: boolean;
  deepseek_base_url: string;
  chat_model: string;
  extract_model: string;
  vision_model: string;
  embed_model: string;
}

export interface SettingsPatch {
  deepseek_api_key?: string;
  deepseek_base_url?: string;
  chat_model?: string;
  extract_model?: string;
  vision_model?: string;
}

export interface NotebookSummary {
  id: number;
  name: string;
  description: string | null;
  document_count: number;
}

export interface Archive {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
  notebooks: NotebookSummary[];
}

export interface NotebookSettings {
  ocr: "auto" | "force" | "off";
  ocr_lang: string[];
  table_structure: boolean;
  chunk_max_tokens: number;
  chunk_overlap_tokens: number;
  describe_figures: boolean;
  extract_concepts: boolean;
  graph_section_depth: number;
}

export interface Notebook {
  id: number;
  archive_id: number;
  archive_name: string;
  name: string;
  description: string | null;
  settings: NotebookSettings;
  created_at: string;
}

export type DocumentStatus =
  | "queued"
  | "parsing"
  | "describing"
  | "indexing"
  | "graphing"
  | "ready"
  | "error";

export interface DocumentSummary {
  id: number;
  notebook_id: number;
  title: string;
  kind: string;
  source_name: string | null;
  status: DocumentStatus;
  status_detail: string | null;
  error: string | null;
  num_pages: number | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentDetail extends DocumentSummary {
  page_sizes: [number, number][];
  block_count: number;
  chunk_count: number;
  figure_count: number;
}

export type BBox = [number, number, number, number];

export interface Block {
  id: number;
  seq: number;
  type: string;
  text: string | null;
  level: number | null;
  page: number | null;
  bbox: BBox | null;
  section_id: number | null;
  image_url: string | null;
  table_html: string | null;
  caption: string | null;
  description: string | null;
  label: string | null;
}

export interface ChunkBlockRef {
  id: number;
  page: number | null;
  bbox: BBox | null;
}

export interface ChunkFigureRef {
  block_id: number;
  label: string | null;
  caption: string | null;
  image_url: string | null;
}

export interface ChunkDetail {
  id: number;
  document_id: number;
  document_title: string;
  document_kind: string;
  kind: "text" | "figure" | "table";
  text: string;
  heading_path: string | null;
  page_start: number | null;
  page_end: number | null;
  blocks: ChunkBlockRef[];
  figures: ChunkFigureRef[];
}

export interface SearchHit {
  chunk_id: number;
  document_id: number;
  document_title: string;
  notebook_id: number;
  kind: string;
  page_start: number | null;
  page_end: number | null;
  heading_path: string | null;
  text: string;
  score: number;
  figures: { block_id: number; label: string | null; caption: string | null }[];
}

// ---------------------------------------------------------------------------
// Graph types (SPEC §8)

export type GraphNodeType =
  | "notebook"
  | "document"
  | "section"
  | "figure"
  | "table"
  | "concept"
  | "topic";

export type GraphTier = "structure" | "concept" | "topic";

export interface GraphNodeRef {
  document_id?: number;
  block_id?: number;
  concept_id?: number;
  notebook_id?: number;
  page?: number;
}

export interface GraphNode {
  id: string;
  type: GraphNodeType;
  label: string;
  x: number;
  y: number;
  size: number;
  tier: GraphTier;
  kind?: string;
  topic?: string | null;
  mastery?: number | null;
  ref?: GraphNodeRef;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  confidence?: string;
}

export interface GraphPayload {
  scope: { kind: "notebook" | "archive"; id: number };
  built_at: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: { documents: number; sections: number; concepts: number; topics: number };
}

export interface HighlightPayload {
  primary: string[];
  related: string[];
}

export interface ConceptMention {
  chunk_id: number;
  evidence: string;
  document_id: number;
  document_title: string;
  page_start: number | null;
}

export interface ConceptRelation {
  relation: string;
  direction: "out" | "in";
  confidence: string;
  other: { id: number; name: string };
}

export interface ConceptDetail {
  id: number;
  name: string;
  kind: string;
  summary: string;
  topic: { id: string; label: string } | null;
  mastery: number | null;
  mentions: ConceptMention[];
  relations: ConceptRelation[];
}

// ---------------------------------------------------------------------------
// Study types (SPEC §9)

export type StudyScopeKind = "notebook" | "document" | "section" | "concept";

export interface StudyScope {
  kind: StudyScopeKind;
  id: number;
}

export interface Flashcard {
  id: number;
  notebook_id: number;
  concept_id: number | null;
  chunk_id: number | null;
  front: string;
  back: string;
  due_at: string;
  reps: number;
  interval_days: number;
  ease: number;
}

export interface QuizQuestion {
  id: number;
  question: string;
  options: string[];
  answer_index: number;
  explanation: string;
  concept_id: number | null;
  chunk_id: number | null;
}

export interface Quiz {
  id: number;
  questions: QuizQuestion[];
}

export interface QuizResult {
  question_id: number;
  correct: boolean;
  answer_index: number;
  explanation: string;
  chunk_id: number | null;
}

export interface QuizSubmitResponse {
  score: number;
  results: QuizResult[];
}

export interface Guide {
  id: number;
  title: string;
  content_md: string;
  created_at: string;
}

export interface StudyPathStep {
  concept_id: number;
  name: string;
  summary: string;
  mastery: number | null;
  node_id: string;
}

export interface StudyPath {
  target: { id: number; name: string };
  steps: StudyPathStep[];
}

export type MasteryMap = Record<string, number>;

export interface NoteContent {
  title: string;
  content_md: string;
}

// ---------------------------------------------------------------------------
// Chat types (SPEC §11)

export interface ChatSession {
  id: string;
  scope: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface CitationBox {
  page: number;
  bbox: BBox;
}

export interface ChunkCitation {
  marker: string;
  kind: "chunk";
  chunk_id: number;
  document_id: number;
  document_title: string;
  document_kind: string;
  page: number | null;
  heading_path: string | null;
  snippet: string;
  boxes: CitationBox[];
}

export interface FigureCitation {
  marker: string;
  kind: "figure";
  block_id: number;
  document_id: number;
  document_title: string;
  label: string | null;
  caption: string | null;
  image_url: string | null;
  page: number | null;
  bbox: BBox | null;
}

export type Citation = ChunkCitation | FigureCitation;

export interface ToolCallRecord {
  id: string;
  name: string;
  arguments?: Record<string, unknown>;
  summary?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  highlight: HighlightPayload | null;
  tool_calls: ToolCallRecord[];
  created_at: string;
}

export interface ChatStatus {
  ready: boolean;
  api_key_set: boolean;
  runtime_found: boolean;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Health / settings

export const getHealth = () => request<HealthStatus>("/health");
export const getSettings = () => request<Settings>("/settings");
export const updateSettings = (patch: SettingsPatch) =>
  request<Settings>("/settings", withBody("PUT", patch));

// ---------------------------------------------------------------------------
// Archives / notebooks

export const listArchives = () => request<Archive[]>("/archives");
export const createArchive = (body: { name: string; description?: string }) =>
  request<Archive>("/archives", withBody("POST", body));
export const updateArchive = (
  id: number,
  patch: { name?: string; description?: string },
) => request<Archive>(`/archives/${id}`, withBody("PATCH", patch));
export const deleteArchive = (id: number) =>
  request<void>(`/archives/${id}`, { method: "DELETE" });

export const createNotebook = (
  archiveId: number,
  body: { name: string; description?: string },
) => request<Notebook>(`/archives/${archiveId}/notebooks`, withBody("POST", body));
export const getNotebook = (id: number) => request<Notebook>(`/notebooks/${id}`);
export const updateNotebook = (
  id: number,
  patch: { name?: string; description?: string; settings?: Partial<NotebookSettings> },
) => request<Notebook>(`/notebooks/${id}`, withBody("PATCH", patch));
export const deleteNotebook = (id: number) =>
  request<void>(`/notebooks/${id}`, { method: "DELETE" });

// ---------------------------------------------------------------------------
// Documents

export const listDocuments = (notebookId: number) =>
  request<DocumentSummary[]>(`/notebooks/${notebookId}/documents`);

export const uploadDocuments = (notebookId: number, files: File[]) => {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  return request<DocumentSummary[]>(`/notebooks/${notebookId}/documents`, {
    method: "POST",
    body: form,
  });
};

export const addUrlDocument = (notebookId: number, url: string) =>
  request<DocumentSummary>(
    `/notebooks/${notebookId}/documents/url`,
    withBody("POST", { url }),
  );

export const addNote = (notebookId: number, body: { title: string; content_md: string }) =>
  request<DocumentSummary>(`/notebooks/${notebookId}/notes`, withBody("POST", body));

export const getNote = (documentId: number) =>
  request<NoteContent>(`/documents/${documentId}/note`);
export const updateNote = (documentId: number, body: NoteContent) =>
  request<DocumentSummary>(`/documents/${documentId}/note`, withBody("PUT", body));

export const getDocument = (id: number) => request<DocumentDetail>(`/documents/${id}`);
export const deleteDocument = (id: number) =>
  request<void>(`/documents/${id}`, { method: "DELETE" });
export const reingestDocument = (id: number) =>
  request<DocumentSummary>(`/documents/${id}/reingest`, { method: "POST" });

export const documentFileUrl = (id: number) => `${BASE}/documents/${id}/file`;

export const getBlocks = (documentId: number, page?: number) =>
  request<Block[]>(
    `/documents/${documentId}/blocks${page != null ? `?page=${page}` : ""}`,
  );

export const blockImageUrl = (blockId: number) => `${BASE}/blocks/${blockId}/image`;

export const getChunk = (id: number) => request<ChunkDetail>(`/chunks/${id}`);

export const search = (scope: string, q: string, k = 8) =>
  request<SearchHit[]>(
    `/search?scope=${encodeURIComponent(scope)}&q=${encodeURIComponent(q)}&k=${k}`,
  );

// ---------------------------------------------------------------------------
// Graph

export const getGraph = (kind: "notebook" | "archive", id: number) =>
  request<GraphPayload>(`/graph/${kind}/${id}`);
export const rebuildGraph = (kind: "notebook" | "archive", id: number) =>
  request<GraphPayload>(`/graph/${kind}/${id}/rebuild`, { method: "POST" });
export const getConcept = (id: number) => request<ConceptDetail>(`/concepts/${id}`);

// ---------------------------------------------------------------------------
// Study

export const generateFlashcards = (body: {
  notebook_id: number;
  scope: StudyScope;
  count?: number;
}) => request<Flashcard[]>("/study/flashcards/generate", withBody("POST", body));

export const listFlashcards = (notebookId: number, dueOnly = false) =>
  request<Flashcard[]>(
    `/study/flashcards?notebook_id=${notebookId}&due_only=${dueOnly}`,
  );

export const reviewFlashcard = (id: number, grade: 0 | 1 | 2) =>
  request<Flashcard>(`/study/flashcards/${id}/review`, withBody("POST", { grade }));

export const deleteFlashcard = (id: number) =>
  request<void>(`/study/flashcards/${id}`, { method: "DELETE" });

export const generateQuiz = (body: {
  notebook_id: number;
  scope: StudyScope;
  count?: number;
}) => request<Quiz>("/study/quiz/generate", withBody("POST", body));

export const submitQuiz = (quizId: number, answers: number[]) =>
  request<QuizSubmitResponse>(`/study/quiz/${quizId}/submit`, withBody("POST", { answers }));

export const generateGuide = (body: { notebook_id: number; scope: StudyScope }) =>
  request<Guide>("/study/guides", withBody("POST", body));

export const listGuides = (notebookId: number) =>
  request<Guide[]>(`/study/guides?notebook_id=${notebookId}`);

export const getStudyPath = (conceptId: number) =>
  request<StudyPath>(`/study/path?concept_id=${conceptId}`);

export const getMastery = (notebookId: number) =>
  request<MasteryMap>(`/study/mastery?notebook_id=${notebookId}`);

// ---------------------------------------------------------------------------
// Chat

export const listChatSessions = (scope: string) =>
  request<ChatSession[]>(`/chat/sessions?scope=${encodeURIComponent(scope)}`);
/** Every thread across all scopes, most recently updated first (the front page). */
export const listAllChatSessions = () => request<ChatSession[]>("/chat/sessions");
export const createChatSession = (scope: string, title?: string) =>
  request<ChatSession>("/chat/sessions", withBody("POST", { scope, title }));
export const deleteChatSession = (id: string) =>
  request<void>(`/chat/sessions/${id}`, { method: "DELETE" });
export const getChatStatus = () => request<ChatStatus>("/chat/status");
export const getChatMessages = (sessionId: string) =>
  request<ChatMessage[]>(`/chat/sessions/${sessionId}/messages`);

export const chatMessagesUrl = (sessionId: string) =>
  `${BASE}/chat/sessions/${sessionId}/messages`;
