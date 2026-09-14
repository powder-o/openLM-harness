import { useCallback, useEffect, useRef, useState } from "react";
import Graph from "graphology";
import Sigma from "sigma";
import { useNavigate } from "react-router-dom";
import * as api from "../api";
import type { ConceptDetail, GraphNode, GraphNodeType } from "../api";
import { count } from "../lib/format";
import { openChunkById, openDocumentRef } from "../lib/reader";
import { useStore } from "../store";
import Wedge from "./Wedge";

type Tier = "structure" | "concept" | "topic";

// Printed in ink: structure in paper whites/greys, concepts in cyan, figures
// in process yellow; magenta marks what the answer cited.
const TYPE_COLORS: Record<GraphNodeType, string> = {
  notebook: "#f3f2f2",
  document: "#f3f2f2",
  section: "#bab6b6",
  figure: "#edbb00",
  table: "#9b9797",
  concept: "#62c5ee",
  topic: "#7d7979",
};

const TYPE_LABELS: [GraphNodeType, string][] = [
  ["document", "source"],
  ["section", "section"],
  ["figure", "figure"],
  ["table", "table"],
  ["concept", "concept"],
  ["topic", "topic"],
];

const CITED = "#ff90b1";
const EDGE = "#3a3736";
const EDGE_DIM = "#242120";
const NODE_DIM = "#3a3736";

/** Weak mastery prints yellow, strong prints cyan. */
function masteryColor(mastery: number | null | undefined): string {
  if (mastery == null) return "#605d5d";
  const t = Math.max(0, Math.min(1, mastery));
  const mix = (a: number, b: number) => Math.round(a + (b - a) * t);
  return `rgb(${mix(0xed, 0x62)}, ${mix(0xbb, 0xc5)}, ${mix(0x00, 0xee)})`;
}

interface NodeAttrs {
  x: number;
  y: number;
  size: number;
  label: string;
  color: string;
  domainType: GraphNodeType;
  tier: Tier;
  kind?: string;
  topic?: string | null;
  mastery?: number | null;
  ref?: GraphNode["ref"];
  [key: string]: unknown;
}

/** Minimal local shadow of sigma's NodeDisplayData / EdgeDisplayData (not part
 * of sigma's public "." export map) — just the fields our reducers set. */
interface NodeDisplayData {
  x: number;
  y: number;
  label: string | null;
  size: number;
  color: string;
  hidden?: boolean;
  forceLabel?: boolean;
  zIndex?: number;
}

interface EdgeDisplayData {
  size?: number;
  color?: string;
  hidden?: boolean;
  zIndex?: number;
}

interface LabelSettings {
  labelSize: number;
  labelFont: string;
  labelWeight: string;
}

/** Sigma's default hover box is white; this one is a dark slug with paper text. */
function drawNodeHover(
  context: CanvasRenderingContext2D,
  data: { x: number; y: number; size: number; label?: string | null; color: string },
  settings: LabelSettings,
) {
  const size = settings.labelSize;
  const label = typeof data.label === "string" ? data.label : "";
  context.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
  context.fillStyle = "#201e1d";
  context.shadowOffsetY = 3;
  context.shadowBlur = 10;
  context.shadowColor = "rgba(0, 0, 0, 0.5)";
  const pad = 5;
  context.beginPath();
  context.arc(data.x, data.y, data.size + pad, 0, Math.PI * 2);
  context.fill();
  if (label) {
    const width = context.measureText(label).width;
    context.fillRect(data.x + data.size + 2, data.y - size / 2 - pad, width + pad * 2 + 4, size + pad * 2);
  }
  context.shadowBlur = 0;
  context.shadowOffsetY = 0;
  context.beginPath();
  context.arc(data.x, data.y, data.size, 0, Math.PI * 2);
  context.fillStyle = data.color;
  context.fill();
  if (label) {
    context.fillStyle = "#f3f2f2";
    context.fillText(label, data.x + data.size + pad + 3, data.y + size / 3);
  }
}

/** The Map pane: the notebook/archive graph at full height (SPEC §8). */
export default function GraphView() {
  const navigate = useNavigate();
  const store = useStore();
  const { scopeKind, graph: payload, graphLoading: loading, graphError } = store;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const handledFocusRef = useRef<number | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [tiers, setTiers] = useState<Record<Tier, boolean>>({ structure: true, concept: true, topic: true });
  const [masteryMode, setMasteryMode] = useState(false);
  const [searchText, setSearchText] = useState("");

  const [detailsNode, setDetailsNode] = useState<GraphNode | null>(null);
  const [conceptDetail, setConceptDetail] = useState<ConceptDetail | null>(null);
  const [conceptLoading, setConceptLoading] = useState(false);
  const [topicMembers, setTopicMembers] = useState<GraphNode[] | null>(null);

  const tiersRef = useRef(tiers);
  const masteryModeRef = useRef(masteryMode);
  const highlightRef = useRef(store.graphHighlight);
  tiersRef.current = tiers;
  masteryModeRef.current = masteryMode;
  highlightRef.current = store.graphHighlight;

  const closeDetails = () => {
    setDetailsNode(null);
    setConceptDetail(null);
    setTopicMembers(null);
  };

  const openConceptDetails = useCallback((node: GraphNode) => {
    const conceptId = node.ref?.concept_id;
    setDetailsNode(node);
    setTopicMembers(null);
    if (conceptId == null) {
      // Archive-scoped concept node (`acon:`): its ref carries `archive_concept_id`, not
      // `concept_id` — there is no per-archive-concept detail endpoint (SPEC §8's
      // GET /api/concepts/{id} is notebook-concept-only), so show what the graph payload
      // already has instead of leaving the click with no visible effect.
      setConceptDetail(null);
      return;
    }
    setConceptLoading(true);
    api
      .getConcept(conceptId)
      .then(setConceptDetail)
      .catch((e) => setError(String(e)))
      .finally(() => setConceptLoading(false));
  }, []);

  const openTopicDetails = useCallback(
    (node: GraphNode) => {
      if (!payload) return;
      setDetailsNode(node);
      setConceptDetail(null);
      setTopicMembers(payload.nodes.filter((n) => n.topic === node.id));
    },
    [payload],
  );

  // Build (or rebuild) the sigma instance whenever the payload changes.
  useEffect(() => {
    if (!payload || !containerRef.current || payload.nodes.length === 0) {
      sigmaRef.current?.kill();
      sigmaRef.current = null;
      graphRef.current = null;
      return;
    }

    const graph = new Graph();
    for (const node of payload.nodes) {
      const color =
        node.type === "concept" && masteryModeRef.current
          ? masteryColor(node.mastery)
          : TYPE_COLORS[node.type] ?? "#7d7979";
      graph.addNode(node.id, {
        x: node.x,
        y: node.y,
        size: node.size,
        label: node.label,
        color,
        domainType: node.type,
        tier: node.tier,
        kind: node.kind,
        topic: node.topic ?? null,
        mastery: node.mastery ?? null,
        ref: node.ref,
      } satisfies NodeAttrs);
    }
    for (const edge of payload.edges) {
      if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
      try {
        graph.addEdgeWithKey(edge.id, edge.source, edge.target, {
          // Not `type`: sigma reads that attribute to pick an edge renderer and
          // throws on relation names like "contains".
          relation: edge.type,
          confidence: edge.confidence,
          color: EDGE,
          size: 1,
        });
      } catch {
        // duplicate edge key, ignore
      }
    }
    graphRef.current = graph;

    // Sigma's public "sigma" export map doesn't expose its Settings/NodeDisplayData
    // types in a way that resolves cleanly under TS "bundler" module resolution,
    // so the reducers below are typed loosely (`any`) at this one interop boundary.
    const nodeReducer = (node: string, data: NodeAttrs): NodeDisplayData => {
      const res: NodeDisplayData = { ...data } as NodeDisplayData;
      if (!tiersRef.current[data.tier]) {
        res.hidden = true;
        return res;
      }
      const hl = highlightRef.current;
      if (hl && (hl.primary.length || hl.related.length)) {
        if (hl.primary.includes(node)) {
          res.size = data.size * 1.6;
          res.color = CITED;
          res.forceLabel = true;
          res.zIndex = 2;
        } else if (hl.related.includes(node)) {
          res.size = data.size * 1.2;
          res.forceLabel = true;
          res.zIndex = 1;
        } else {
          res.color = NODE_DIM;
          res.label = null;
          res.zIndex = 0;
        }
      }
      return res;
    };

    const edgeReducer = (edge: string, data: EdgeDisplayData): EdgeDisplayData => {
      const res: EdgeDisplayData = { ...data };
      const g = graphRef.current;
      if (!g) return res;
      const source = g.source(edge);
      const target = g.target(edge);
      const sourceTier = g.getNodeAttribute(source, "tier") as Tier;
      const targetTier = g.getNodeAttribute(target, "tier") as Tier;
      if (!tiersRef.current[sourceTier] || !tiersRef.current[targetTier]) {
        res.hidden = true;
        return res;
      }
      const hl = highlightRef.current;
      if (hl && (hl.primary.length || hl.related.length)) {
        const inHl = (n: string) => hl.primary.includes(n) || hl.related.includes(n);
        if (inHl(source) && inHl(target)) {
          res.color = hl.primary.includes(source) || hl.primary.includes(target) ? "#8f5a6b" : "#3f6b7d";
          res.size = 1.5;
        } else {
          res.color = EDGE_DIM;
        }
      }
      return res;
    };

    const sigma = new Sigma(graph, containerRef.current, {
      labelRenderedSizeThreshold: 6,
      defaultNodeColor: "#7d7979",
      defaultEdgeColor: EDGE,
      labelColor: { color: "#bab6b6" },
      labelFont: '"Source Serif 4 Variable", Georgia, serif',
      labelSize: 12,
      labelWeight: "400",
      zIndex: true,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      defaultDrawNodeHover: drawNodeHover as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      nodeReducer: nodeReducer as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      edgeReducer: edgeReducer as any,
    });

    sigma.on("clickNode", ({ node }) => {
      const attrs = graph.getNodeAttributes(node) as NodeAttrs;
      const graphNode: GraphNode = {
        id: node,
        type: attrs.domainType,
        label: attrs.label,
        x: attrs.x,
        y: attrs.y,
        size: attrs.size,
        tier: attrs.tier,
        kind: attrs.kind,
        topic: attrs.topic,
        mastery: attrs.mastery,
        ref: attrs.ref,
      };
      if (attrs.domainType === "concept") {
        store.setSelectedNode({ nodeId: node, type: "concept", conceptId: attrs.ref?.concept_id, label: attrs.label });
        openConceptDetails(graphNode);
      } else if (attrs.domainType === "topic") {
        openTopicDetails(graphNode);
      } else if (attrs.domainType === "notebook") {
        if (attrs.ref?.notebook_id) navigate(`/notebook/${attrs.ref.notebook_id}`);
      } else if (attrs.domainType === "section") {
        store.setSelectedNode({ nodeId: node, type: "section", sectionBlockId: attrs.ref?.block_id, label: attrs.label });
        if (attrs.ref?.document_id != null) {
          openDocumentRef(store.openReader, {
            document_id: attrs.ref.document_id,
            block_id: attrs.ref.block_id,
            page: attrs.ref.page,
          });
        }
      } else if (attrs.ref?.document_id != null) {
        openDocumentRef(store.openReader, {
          document_id: attrs.ref.document_id,
          block_id: attrs.ref.block_id,
          page: attrs.ref.page,
        });
      }
    });

    sigmaRef.current = sigma;

    return () => {
      sigma.kill();
      sigmaRef.current = null;
      graphRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload]);

  // Re-render (not rebuild) when toggle/highlight state changes.
  useEffect(() => {
    sigmaRef.current?.refresh();
  }, [tiers, masteryMode, store.graphHighlight]);

  // Recolor concept nodes when mastery mode toggles, without rebuilding the graph.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !payload) return;
    for (const node of payload.nodes) {
      if (node.type !== "concept" || !graph.hasNode(node.id)) continue;
      graph.setNodeAttribute(node.id, "color", masteryMode ? masteryColor(node.mastery) : TYPE_COLORS.concept);
    }
    sigmaRef.current?.refresh();
  }, [masteryMode, payload]);

  // Pan to a node asked for elsewhere ("Show in map", a path step, a concept tag).
  useEffect(() => {
    const focus = store.graphFocus;
    const graph = graphRef.current;
    if (!focus || !graph || !sigmaRef.current || handledFocusRef.current === focus.nonce) return;
    if (!graph.hasNode(focus.nodeId)) return;
    handledFocusRef.current = focus.nonce;
    const display = sigmaRef.current.getNodeDisplayData(focus.nodeId);
    if (display) sigmaRef.current.getCamera().animate({ x: display.x, y: display.y, ratio: 0.35 }, { duration: 400 });
    store.setGraphHighlight({ primary: [focus.nodeId], related: [] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.graphFocus, payload]);

  const { closePane } = store;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.key !== "Escape" || el?.closest("input, textarea, select") || document.querySelector(".dialog-backdrop")) {
        return;
      }
      closePane();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closePane]);

  async function handleRebuild() {
    setRebuilding(true);
    await store.rebuildGraph();
    setRebuilding(false);
  }

  function handleSearch() {
    if (!payload || !searchText.trim() || !sigmaRef.current) return;
    const needle = searchText.trim().toLowerCase();
    const match = payload.nodes.find((n) => n.label.toLowerCase().includes(needle));
    if (!match) {
      setError(`Nothing on the map matches “${searchText.trim()}”.`);
      return;
    }
    setError(null);
    const display = sigmaRef.current.getNodeDisplayData(match.id);
    if (display) sigmaRef.current.getCamera().animate({ x: display.x, y: display.y, ratio: 0.35 }, { duration: 400 });
    store.setGraphHighlight({ primary: [match.id], related: [] });
  }

  function focusOtherConcept(otherId: number) {
    store.focusGraphNode(`${scopeKind === "archive" ? "acon" : "con"}:${otherId}`);
  }

  const stats = payload?.stats;
  const hasHighlight = !!store.graphHighlight && store.graphHighlight.primary.length + store.graphHighlight.related.length > 0;

  return (
    <section className="pane" aria-label="Map">
      <header className="pane-head">
        <div className="pane-head-main">
          <div className="kicker">Map · {scopeKind}</div>
          <div className="pane-title">
            <span className="pane-title-text">{hasHighlight ? "What the answer touched" : "Everything in reach"}</span>
            {stats && (
              <span className="pane-title-meta">
                {" "}
                · {count(stats.documents, "source")} · {count(stats.sections, "section")} ·{" "}
                {count(stats.concepts, "concept")} · {count(stats.topics, "topic")}
              </span>
            )}
          </div>
        </div>
        <div className="pane-tools">
          <button type="button" className="btn btn-secondary btn-sm" onClick={store.closePane} title="Close the map (Esc)">
            Close
          </button>
        </div>
      </header>

      <div className="map-toolbar">
        <div className="seg" aria-label="Layers">
          {(["structure", "concept", "topic"] as Tier[]).map((t) => (
            <button
              key={t}
              type="button"
              aria-pressed={tiers[t]}
              className={`seg-opt ${tiers[t] ? "is-on" : ""}`}
              onClick={() => setTiers((s) => ({ ...s, [t]: !s[t] }))}
            >
              {t === "structure" ? "Structure" : t === "concept" ? "Concepts" : "Topics"}
            </button>
          ))}
        </div>
        <input
          className="input input-sm map-search"
          placeholder="Find on the map…"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
        />
        <label className="check">
          <input type="checkbox" checked={masteryMode} onChange={(e) => setMasteryMode(e.target.checked)} />
          Colour by mastery
        </label>
        <span className="map-toolbar-spacer" />
        {hasHighlight && (
          <button type="button" className="link" onClick={() => store.setGraphHighlight(null)}>
            Clear highlight
          </button>
        )}
        <button type="button" className="link link-quiet" onClick={handleRebuild} disabled={rebuilding}>
          {rebuilding ? "Rebuilding…" : "Rebuild"}
        </button>
      </div>

      <div className="map-stage">
        {(error || graphError) && <p className="map-message error-text">{error ?? graphError}</p>}
        {loading && !payload && <p className="pane-empty">Loading the map…</p>}
        {!loading && payload && payload.nodes.length === 0 && (
          <p className="pane-empty">No map yet. Add sources to this {scopeKind} and it builds as they're indexed.</p>
        )}
        {payload && payload.nodes.length > 0 && <div ref={containerRef} className="map-canvas" />}

        {payload && payload.nodes.length > 0 && (
          <div className="map-legend">
            {masteryMode ? (
              <span>
                <i style={{ background: masteryColor(0) }} />
                weak
                <i style={{ background: masteryColor(0.5), marginLeft: 10 }} />
                <i style={{ background: masteryColor(1) }} />
                strong
              </span>
            ) : (
              TYPE_LABELS.map(([type, label]) => (
                <span key={type}>
                  <i style={{ background: TYPE_COLORS[type] }} />
                  {label}
                </span>
              ))
            )}
            {hasHighlight && (
              <span>
                <i style={{ background: CITED }} />
                cited
              </span>
            )}
          </div>
        )}

        {detailsNode && (
          <aside className="card map-details">
            <div className="dialog-head">
              <span className="label-caps">{detailsNode.type === "topic" ? "Topic" : conceptDetail?.kind ?? detailsNode.kind ?? detailsNode.type}</span>
              <button type="button" className="dialog-close" aria-label="Close details" onClick={closeDetails}>
                ×
              </button>
            </div>
            <h4 className="map-details-title">{detailsNode.label}</h4>

            {detailsNode.type === "concept" && (
              <>
                {conceptLoading && <p className="hint">Loading…</p>}
                {conceptDetail && (
                  <>
                    <p className="map-details-summary">{conceptDetail.summary}</p>
                    {conceptDetail.mastery != null && (
                      <div className="map-details-row">
                        <Wedge value={conceptDetail.mastery} wide />
                        <span className="hint">mastery {Math.round(conceptDetail.mastery * 100)}%</span>
                      </div>
                    )}
                    {conceptDetail.topic && (
                      <p className="map-details-line">
                        Topic{" "}
                        <button
                          type="button"
                          className="link"
                          onClick={() =>
                            store.focusGraphNode(
                              String(conceptDetail.topic!.id).startsWith("topic:")
                                ? conceptDetail.topic!.id
                                : `topic:${conceptDetail.topic!.id}`,
                            )
                          }
                        >
                          {conceptDetail.topic.label}
                        </button>
                      </p>
                    )}
                    {conceptDetail.relations.length > 0 && (
                      <div className="map-details-block">
                        <div className="kicker">Relations</div>
                        <ul className="plain-list">
                          {conceptDetail.relations.map((r, i) => (
                            <li key={i}>
                              <span className="faint">{r.relation.replace(/_/g, " ")}{r.direction === "in" ? " (from)" : ""}</span>{" "}
                              <button type="button" className="link" onClick={() => focusOtherConcept(r.other.id)}>
                                {r.other.name}
                              </button>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {conceptDetail.mentions.length > 0 && (
                      <div className="map-details-block">
                        <div className="kicker">Mentions · {conceptDetail.mentions.length}</div>
                        <ul className="plain-list">
                          {conceptDetail.mentions.map((m) => (
                            <li key={m.chunk_id}>
                              <button type="button" className="link" onClick={() => openChunkById(m.chunk_id, store.openReader)}>
                                {m.document_title}
                                {m.page_start != null ? ` · p.${m.page_start}` : ""}
                              </button>
                              <div className="map-details-evidence">“{m.evidence}”</div>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <div className="map-details-actions">
                      <button type="button" className="btn btn-primary btn-sm" onClick={() => store.openStudyPath(conceptDetail.id)}>
                        Learning path
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => store.setComposerDraft(`Explain ${conceptDetail.name}, with sources.`)}
                      >
                        Ask about it
                      </button>
                    </div>
                  </>
                )}
                {!conceptLoading && !conceptDetail && detailsNode.ref?.concept_id == null && (
                  <>
                    {detailsNode.mastery != null && <Wedge value={detailsNode.mastery} wide />}
                    {detailsNode.topic && (
                      <p className="map-details-line">
                        Topic{" "}
                        <button type="button" className="link" onClick={() => store.focusGraphNode(detailsNode.topic!)}>
                          {payload?.nodes.find((n) => n.id === detailsNode.topic)?.label ?? detailsNode.topic}
                        </button>
                      </p>
                    )}
                    <p className="hint">Merged across notebooks in this archive.</p>
                  </>
                )}
              </>
            )}

            {detailsNode.type === "topic" && topicMembers && (
              <div className="map-details-block">
                <div className="kicker">Concepts in this topic · {topicMembers.length}</div>
                <div className="tag-row">
                  {topicMembers.map((m) => (
                    <button key={m.id} type="button" className="tag" onClick={() => openConceptDetails(m)}>
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </aside>
        )}
      </div>
    </section>
  );
}
