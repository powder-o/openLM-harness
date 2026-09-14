// Workspace store shared by a notebook/archive page and everything nested in
// it (rail sections, Conversation, EvidenceRail, Reader/Map panes, Study).
// Deliberately not a state library: the handful of cross-panel things that
// have to flow between panels live here:
//   - which rail tab is showing, and whether a Reader/Map pane has taken over
//   - the scope's threads, documents, due cards and graph payload
//   - a citation/figure/graph-node click -> open the Reader at a target
//   - the latest answer -> the evidence rail and the map highlight
//   - selecting text in the Reader -> "Ask about this" prefills the composer
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import * as api from "./api";
import type {
  BBox,
  ChatMessage,
  ChatSession,
  DocumentSummary,
  GraphPayload,
  HighlightPayload,
} from "./api";

export type ScopeKind = "notebook" | "archive";
export type RailTab = "evidence" | "sources" | "study" | "notebooks";
export type Pane = "reader" | "map";
export type StudyView = "flashcards" | "quiz" | "guides" | "path";

export interface ReaderBox {
  page: number;
  bbox: BBox;
}

/** Where a Reader target came from, so the pane can say "Opened from your
 * answer · p.144" and the matching citation in the thread can light up. */
export interface ReaderOrigin {
  messageId: string;
  marker: string;
  label: string;
  heading?: string | null;
}

export interface ReaderTargetInput {
  documentId: number;
  page?: number;
  blockId?: number;
  boxes?: ReaderBox[];
  origin?: ReaderOrigin;
}

export interface ReaderTarget extends ReaderTargetInput {
  nonce: number;
}

/** The last graph node the user clicked (concept or section), used by the
 * Study scope picker ("the currently selected graph concept or section"). */
export interface SelectedGraphNode {
  nodeId: string;
  type: string;
  conceptId?: number;
  sectionBlockId?: number;
  label: string;
}

export interface GraphFocus {
  nodeId: string;
  nonce: number;
}

export interface StudyTarget {
  view: StudyView;
  conceptId?: number;
  nonce: number;
}

interface StoreState {
  scopeKind: ScopeKind;
  scopeId: number;
  scope: string;

  railTab: RailTab;
  setRailTab: (tab: RailTab) => void;
  evidenceHasUpdate: boolean;

  pane: Pane | null;
  closePane: () => void;
  openMap: () => void;

  threads: ChatSession[];
  threadId: string | null;
  setThreadId: (id: string | null) => void;
  /** Start a blank thread; the session is created on the first question. */
  newThread: () => void;
  ensureThread: () => Promise<string>;
  deleteThread: (id: string) => Promise<void>;
  refreshThreads: () => void;

  documents: DocumentSummary[];
  documentsLoaded: boolean;
  refreshDocuments: () => void;
  dueCount: number;
  refreshDue: () => void;

  graph: GraphPayload | null;
  graphLoading: boolean;
  graphError: string | null;
  rebuildGraph: () => Promise<void>;

  lastAnswer: ChatMessage | null;
  setLastAnswer: (message: ChatMessage | null, fresh?: boolean) => void;

  readerTarget: ReaderTarget | null;
  openReader: (target: ReaderTargetInput) => void;

  graphHighlight: HighlightPayload | null;
  setGraphHighlight: (highlight: HighlightPayload | null) => void;

  selectedNode: SelectedGraphNode | null;
  setSelectedNode: (node: SelectedGraphNode | null) => void;

  graphFocus: GraphFocus | null;
  focusGraphNode: (nodeId: string) => void;

  composerDraft: string;
  setComposerDraft: (text: string) => void;

  studyTarget: StudyTarget | null;
  openStudyPath: (conceptId: number) => void;
  setStudyView: (view: StudyView) => void;
}

const StoreContext = createContext<StoreState | null>(null);

const ACTIVE_STATUSES = new Set(["queued", "parsing", "describing", "indexing", "graphing"]);

interface ProviderProps {
  scopeKind: ScopeKind;
  scopeId: number;
  /** e.g. "sources" when arriving from "Add to a notebook". */
  initialRailTab?: RailTab;
  children: ReactNode;
}

export function StoreProvider({ scopeKind, scopeId, initialRailTab, children }: ProviderProps) {
  const scope = `${scopeKind}:${scopeId}`;
  const [railTab, setRailTabState] = useState<RailTab>(initialRailTab ?? "evidence");
  const [evidenceHasUpdate, setEvidenceHasUpdate] = useState(false);
  const [pane, setPane] = useState<Pane | null>(null);

  const [threads, setThreads] = useState<ChatSession[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const threadsRef = useRef<ChatSession[]>([]);
  threadsRef.current = threads;

  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [documentsLoaded, setDocumentsLoaded] = useState(false);
  const [docPollGen, setDocPollGen] = useState(0);
  const [dueCount, setDueCount] = useState(0);
  const [dueGen, setDueGen] = useState(0);

  const [graph, setGraph] = useState<GraphPayload | null>(null);
  const [graphLoading, setGraphLoading] = useState(true);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [graphGen, setGraphGen] = useState(0);

  const [lastAnswer, setLastAnswerState] = useState<ChatMessage | null>(null);
  const [readerTarget, setReaderTarget] = useState<ReaderTarget | null>(null);
  const [graphHighlight, setGraphHighlight] = useState<HighlightPayload | null>(null);
  const [selectedNode, setSelectedNode] = useState<SelectedGraphNode | null>(null);
  const [graphFocus, setGraphFocus] = useState<GraphFocus | null>(null);
  const [composerDraft, setComposerDraft] = useState("");
  const [studyTarget, setStudyTarget] = useState<StudyTarget | null>(null);
  const nonceRef = useRef(0);
  const railTabRef = useRef(railTab);
  const paneRef = useRef(pane);
  railTabRef.current = railTab;
  paneRef.current = pane;

  // Threads ---------------------------------------------------------------

  const refreshThreads = useCallback(() => {
    api
      .listChatSessions(scope)
      .then(setThreads)
      .catch(() => {});
  }, [scope]);

  useEffect(() => {
    let cancelled = false;
    api
      .listChatSessions(scope)
      .then((list) => {
        if (cancelled) return;
        setThreads(list);
        setThreadId(list[0]?.id ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [scope]);

  const newThread = useCallback(() => {
    setThreadId(null);
    setPane(null);
  }, []);

  const ensureThread = useCallback(async () => {
    if (threadId) return threadId;
    const session = await api.createChatSession(scope);
    setThreads((t) => [session, ...t]);
    setThreadId(session.id);
    return session.id;
  }, [scope, threadId]);

  const deleteThread = useCallback(async (id: string) => {
    await api.deleteChatSession(id);
    const rest = threadsRef.current.filter((t) => t.id !== id);
    setThreads(rest);
    setThreadId((cur) => (cur === id ? rest[0]?.id ?? null : cur));
  }, []);

  // Documents + due cards (notebook scope only) ----------------------------

  const refreshDocuments = useCallback(() => setDocPollGen((g) => g + 1), []);
  const refreshDue = useCallback(() => setDueGen((g) => g + 1), []);

  useEffect(() => {
    if (scopeKind !== "notebook") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let readyIds: string | null = null;
    async function tick() {
      try {
        const docs = await api.listDocuments(scopeId);
        if (cancelled) return;
        setDocuments(docs);
        setDocumentsLoaded(true);
        // A source finishing ingestion changes the graph; pick that up.
        const ready = docs
          .filter((d) => d.status === "ready")
          .map((d) => d.id)
          .join(",");
        if (readyIds !== null && ready !== readyIds) setGraphGen((g) => g + 1);
        readyIds = ready;
        if (docs.some((d) => ACTIVE_STATUSES.has(d.status))) timer = setTimeout(tick, 2000);
      } catch {
        if (!cancelled) timer = setTimeout(tick, 4000);
      }
    }
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [scopeKind, scopeId, docPollGen]);

  useEffect(() => {
    if (scopeKind !== "notebook") return;
    api
      .listFlashcards(scopeId, true)
      .then((cards) => setDueCount(cards.length))
      .catch(() => {});
  }, [scopeKind, scopeId, dueGen]);

  // Graph -------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    setGraphLoading(true);
    setGraphError(null);
    api
      .getGraph(scopeKind, scopeId)
      .then((p) => !cancelled && setGraph(p))
      .catch((e) => !cancelled && setGraphError(String(e)))
      .finally(() => !cancelled && setGraphLoading(false));
    return () => {
      cancelled = true;
    };
  }, [scopeKind, scopeId, graphGen]);

  const rebuildGraph = useCallback(async () => {
    setGraphError(null);
    try {
      setGraph(await api.rebuildGraph(scopeKind, scopeId));
    } catch (e) {
      setGraphError(String(e));
    }
  }, [scopeKind, scopeId]);

  // Navigation between panels ----------------------------------------------

  const setRailTab = useCallback((tab: RailTab) => {
    setRailTabState(tab);
    setPane(null);
    if (tab === "evidence") setEvidenceHasUpdate(false);
  }, []);

  const closePane = useCallback(() => {
    setPane(null);
    if (railTabRef.current === "evidence") setEvidenceHasUpdate(false);
  }, []);
  const openMap = useCallback(() => setPane("map"), []);

  const setLastAnswer = useCallback((message: ChatMessage | null, fresh = false) => {
    setLastAnswerState(message);
    setGraphHighlight(message?.highlight ?? null);
    if (fresh && message && (railTabRef.current !== "evidence" || paneRef.current)) {
      setEvidenceHasUpdate(true);
    }
  }, []);

  const openReader = useCallback((target: ReaderTargetInput) => {
    nonceRef.current += 1;
    setReaderTarget({ ...target, nonce: nonceRef.current });
    setPane("reader");
  }, []);

  const focusGraphNode = useCallback((nodeId: string) => {
    nonceRef.current += 1;
    setGraphFocus({ nodeId, nonce: nonceRef.current });
    setPane("map");
  }, []);

  const openStudyPath = useCallback((conceptId: number) => {
    nonceRef.current += 1;
    setStudyTarget({ view: "path", conceptId, nonce: nonceRef.current });
    setRailTabState("study");
    setPane(null);
  }, []);

  const setStudyView = useCallback((view: StudyView) => {
    nonceRef.current += 1;
    setStudyTarget((prev) => ({ view, conceptId: prev?.conceptId, nonce: nonceRef.current }));
  }, []);

  const value = useMemo<StoreState>(
    () => ({
      scopeKind,
      scopeId,
      scope,
      railTab,
      setRailTab,
      evidenceHasUpdate,
      pane,
      closePane,
      openMap,
      threads,
      threadId,
      setThreadId,
      newThread,
      ensureThread,
      deleteThread,
      refreshThreads,
      documents,
      documentsLoaded,
      refreshDocuments,
      dueCount,
      refreshDue,
      graph,
      graphLoading,
      graphError,
      rebuildGraph,
      lastAnswer,
      setLastAnswer,
      readerTarget,
      openReader,
      graphHighlight,
      setGraphHighlight,
      selectedNode,
      setSelectedNode,
      graphFocus,
      focusGraphNode,
      composerDraft,
      setComposerDraft,
      studyTarget,
      openStudyPath,
      setStudyView,
    }),
    [
      scopeKind,
      scopeId,
      scope,
      railTab,
      setRailTab,
      evidenceHasUpdate,
      pane,
      closePane,
      openMap,
      threads,
      threadId,
      newThread,
      ensureThread,
      deleteThread,
      refreshThreads,
      documents,
      documentsLoaded,
      refreshDocuments,
      dueCount,
      refreshDue,
      graph,
      graphLoading,
      graphError,
      rebuildGraph,
      lastAnswer,
      setLastAnswer,
      readerTarget,
      openReader,
      graphHighlight,
      selectedNode,
      graphFocus,
      focusGraphNode,
      composerDraft,
      studyTarget,
      openStudyPath,
      setStudyView,
    ],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): StoreState {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used within a StoreProvider");
  return ctx;
}
