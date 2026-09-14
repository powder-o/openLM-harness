import { useMemo } from "react";
import type { Citation, GraphNode, GraphNodeType, GraphPayload, HighlightPayload } from "../api";
import { citationLabel, extractMarkers, findCitation } from "../lib/citations";
import { clip, kindMark, kindName, lastHeading } from "../lib/format";
import { openCitation } from "../lib/reader";
import { useStore } from "../store";

/** The Evidence tab: a standing minimap of what the latest answer touched,
 * the passages it cited, and the concepts within reach of it. */
export default function EvidenceRail() {
  const store = useStore();
  const answer = store.lastAnswer;
  const highlight = answer?.highlight ?? null;

  const cited = useMemo(() => {
    if (!answer) return [];
    const out: Citation[] = [];
    const seen = new Set<string>();
    for (const m of extractMarkers(answer.content)) {
      const c = findCitation(answer.citations, m.marker);
      if (!c) continue;
      const key = c.kind === "chunk" ? `c${c.chunk_id}` : `f${c.block_id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(c);
    }
    return out;
  }, [answer]);

  const concepts = useMemo(() => conceptsInReach(store.graph, highlight), [store.graph, highlight]);

  function open(citation: Citation) {
    if (!answer) return;
    openCitation(citation, (target) =>
      store.openReader({
        ...target,
        origin: {
          messageId: answer.id,
          marker: citation.marker,
          label: citationLabel(citation),
          heading: citation.kind === "chunk" ? citation.heading_path : citation.label,
        },
      }),
    ).catch((e) => console.error(e));
  }

  const touched = !!highlight && highlight.primary.length + highlight.related.length > 0;

  return (
    <div className="side-panel">
      <section className="side-section">
        <div className="side-section-head">
          <span className="kicker">{touched ? "Map of what this answer touched" : "Map"}</span>
          <button type="button" className="link side-link" onClick={store.openMap}>
            Open map
          </button>
        </div>
        <MiniMap
          payload={store.graph}
          highlight={touched ? highlight : null}
          loading={store.graphLoading}
          onOpen={store.openMap}
          onNode={store.focusGraphNode}
        />
      </section>

      <section className="side-section">
        <div className="kicker">Cited in this answer · {cited.length}</div>
        {!answer && (
          <p className="hint">
            Ask a question — the passages behind the answer collect here, each one a click from its page.
          </p>
        )}
        {answer && cited.length === 0 && <p className="hint">This answer didn't cite a passage.</p>}
        {cited.map((c) => (
          <EvidenceCard key={c.marker} citation={c} onOpen={() => open(c)} />
        ))}
      </section>

      {concepts.length > 0 && (
        <section className="side-section">
          <div className="kicker">Concepts in reach</div>
          <div className="tag-row">
            {concepts.map(({ node, touched: isTouched }) => (
              <button
                key={node.id}
                type="button"
                className={`tag ${isTouched ? "tag-cyan" : ""}`}
                onClick={() => store.focusGraphNode(node.id)}
                title="Show in map"
              >
                {node.label}
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function EvidenceCard({ citation, onOpen }: { citation: Citation; onOpen: () => void }) {
  const box =
    citation.kind === "chunk" ? citation.boxes[0]?.bbox : citation.bbox ?? undefined;
  const heading = lastHeading(citation.kind === "chunk" ? citation.heading_path : null, 22);
  const kind = citation.kind === "chunk" ? citation.document_kind : "pdf";
  const place = citation.page != null ? `p.${citation.page}` : `${kindName(kind)} · no pages`;

  let thumb;
  if (citation.kind === "figure" && citation.image_url) {
    thumb = (
      <span className="thumb thumb-image">
        <img src={citation.image_url} alt="" />
      </span>
    );
  } else if (citation.page != null) {
    const top = box ? Math.min(88, box[1] * 100) : 40;
    const height = box ? Math.max(8, Math.min(40, (box[3] - box[1]) * 100)) : 10;
    thumb = (
      <span className="thumb thumb-page">
        <span className="thumb-lines" />
        <span
          className="thumb-mark"
          style={{
            top: `${top}%`,
            height: `${height}%`,
            left: box ? `${Math.max(8, box[0] * 100)}%` : "12%",
            right: box ? `${Math.max(8, (1 - box[2]) * 100)}%` : "24%",
          }}
        />
      </span>
    );
  } else {
    thumb = <span className="thumb thumb-kind">{kindMark(kind)}</span>;
  }

  return (
    <button type="button" className="evidence-card" onClick={onOpen}>
      {thumb}
      <span className="evidence-card-text">
        <span className="label-caps">
          {citation.kind === "figure" ? `${citation.label ?? "Figure"} · ${place}` : place}
          {heading ? ` · § ${heading}` : ""}
        </span>
        <span className="evidence-card-title">{citation.document_title}</span>
        <span className="evidence-card-snippet">
          {citation.kind === "chunk"
            ? `“${clip(citation.snippet.trim(), 180)}”`
            : citation.caption ?? "Figure from this source."}
        </span>
      </span>
    </button>
  );
}

// ─── Concepts in reach ─────────────────────────────────────────────────────

function conceptsInReach(payload: GraphPayload | null, highlight: HighlightPayload | null) {
  if (!payload || !highlight) return [];
  const byId = new Map(payload.nodes.map((n) => [n.id, n]));
  const touchedIds = [...new Set([...highlight.primary, ...highlight.related])].filter(
    (id) => byId.get(id)?.type === "concept",
  );
  const touched = new Set(touchedIds);
  const near: string[] = [];
  for (const e of payload.edges) {
    for (const [a, b] of [
      [e.source, e.target],
      [e.target, e.source],
    ]) {
      if (touched.has(a) && !touched.has(b) && byId.get(b)?.type === "concept" && !near.includes(b)) near.push(b);
    }
  }
  return [
    ...touchedIds.slice(0, 8).map((id) => ({ node: byId.get(id)!, touched: true })),
    ...near.slice(0, 4).map((id) => ({ node: byId.get(id)!, touched: false })),
  ];
}

// ─── Minimap ───────────────────────────────────────────────────────────────

const W = 356;
const H = 206;
const PAD_X = 40;
const PAD_Y = 16;

const INK = {
  cited: "#ff90b1",
  related: "#62c5ee",
  edge: "rgba(243,242,242,0.14)",
};

const TYPE_FILL: Record<GraphNodeType, string> = {
  notebook: "#f3f2f2",
  document: "#f3f2f2",
  section: "#bab6b6",
  figure: "#edbb00",
  table: "#7d7979",
  concept: "#62c5ee",
  topic: "#605d5d",
};

const STRUCTURE_RANK: Partial<Record<GraphNodeType, number>> = { document: 0, section: 1, figure: 2, table: 3 };

interface MiniMapProps {
  payload: GraphPayload | null;
  highlight: HighlightPayload | null;
  loading: boolean;
  onOpen: () => void;
  onNode: (nodeId: string) => void;
}

function MiniMap({ payload, highlight, loading, onOpen, onNode }: MiniMapProps) {
  const layout = useMemo(() => {
    if (!payload || payload.nodes.length === 0) return null;
    const byId = new Map(payload.nodes.map((n) => [n.id, n]));
    const primary = new Set(highlight?.primary ?? []);
    const related = new Set(highlight?.related ?? []);
    let nodes: GraphNode[];

    if (highlight) {
      const chosen = new Set([...primary, ...related].filter((id) => byId.has(id)));
      const structural = new Set<string>();
      for (const e of payload.edges) {
        for (const [a, b] of [
          [e.source, e.target],
          [e.target, e.source],
        ]) {
          const other = byId.get(b);
          if (chosen.has(a) && !chosen.has(b) && other && STRUCTURE_RANK[other.type] != null) structural.add(b);
        }
      }
      [...structural]
        .sort((x, y) => STRUCTURE_RANK[byId.get(x)!.type]! - STRUCTURE_RANK[byId.get(y)!.type]!)
        .slice(0, 8)
        .forEach((id) => chosen.add(id));
      nodes = [...chosen].map((id) => byId.get(id)!);
    } else {
      nodes = [...payload.nodes].sort((a, b) => b.size - a.size).slice(0, 60);
    }
    if (nodes.length === 0) return null;

    const xs = nodes.map((n) => n.x);
    const ys = nodes.map((n) => n.y);
    const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const sizes = nodes.map((n) => n.size);
    const [minS, maxS] = [Math.min(...sizes), Math.max(...sizes)];
    const bottom = H - 34; // room below the lowest nodes for their labels and the legend
    const px = (x: number) => (maxX === minX ? W / 2 : PAD_X + ((x - minX) / (maxX - minX)) * (W - 2 * PAD_X));
    const py = (y: number) => (maxY === minY ? H / 2 : bottom - ((y - minY) / (maxY - minY)) * (bottom - PAD_Y));

    const ids = new Set(nodes.map((n) => n.id));
    const hot = new Set([...primary, ...related]);
    const edges = payload.edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .slice(0, 400)
      .map((e) => ({
        id: e.id,
        x1: px(byId.get(e.source)!.x),
        y1: py(byId.get(e.source)!.y),
        x2: px(byId.get(e.target)!.x),
        y2: py(byId.get(e.target)!.y),
        hot: hot.has(e.source) && hot.has(e.target) && (primary.has(e.source) || primary.has(e.target)),
      }));

    const labelled = new Set(
      highlight
        ? nodes.filter((n) => nodes.length <= 18 || primary.has(n.id) || n.type === "document").map((n) => n.id)
        : [...nodes].slice(0, 8).map((n) => n.id),
    );

    const drawn = nodes.map((n) => {
      const isPrimary = primary.has(n.id);
      const isRelated = related.has(n.id);
      const r = highlight
        ? isPrimary
          ? 7
          : isRelated
            ? 5
            : n.type === "document"
              ? 6
              : 4
        : 2.5 + (maxS === minS ? 0.5 : (n.size - minS) / (maxS - minS)) * 3.5;
      const cx = px(n.x);
      const cy = py(n.y);
      return {
        node: n,
        cx,
        cy,
        r,
        fill: isPrimary ? INK.cited : isRelated ? INK.related : TYPE_FILL[n.type] ?? "#7d7979",
        strong: isPrimary || isRelated,
        label: labelled.has(n.id) ? clip(n.label, 22) : null,
        labelX: cx,
        labelY: cy + r + 10,
      };
    });
    // Place labels strongest first, kept inside the frame, dropping any that would collide.
    const placed: { x1: number; x2: number; y1: number; y2: number }[] = [];
    for (const d of [...drawn].sort((a, b) => Number(b.strong) - Number(a.strong))) {
      if (!d.label) continue;
      const half = (d.label.length * 4.3) / 2 + 2;
      d.labelX = Math.min(W - half, Math.max(half, d.cx));
      const box = { x1: d.labelX - half, x2: d.labelX + half, y1: d.labelY - 9, y2: d.labelY + 2 };
      if (placed.some((p) => p.x1 < box.x2 && box.x1 < p.x2 && p.y1 < box.y2 && box.y1 < p.y2)) {
        d.label = null;
        continue;
      }
      placed.push(box);
    }
    // Draw emphasised nodes last so they sit on top.
    drawn.sort((a, b) => Number(a.strong) - Number(b.strong));
    return { edges, drawn };
  }, [payload, highlight]);

  if (loading && !payload) return <div className="minimap minimap-empty">Loading map…</div>;
  if (!layout) {
    return <div className="minimap minimap-empty">No map yet — it builds as sources are indexed.</div>;
  }

  return (
    <div className="minimap">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Map of the notebook" onClick={onOpen}>
        <g>
          {layout.edges.map((e) => (
            <line
              key={e.id}
              x1={e.x1}
              y1={e.y1}
              x2={e.x2}
              y2={e.y2}
              stroke={e.hot ? INK.cited : INK.edge}
              strokeWidth={e.hot ? 1.4 : 1}
            />
          ))}
        </g>
        <g>
          {layout.drawn.map((d) => (
            <g
              key={d.node.id}
              className="minimap-node"
              onClick={(ev) => {
                ev.stopPropagation();
                onNode(d.node.id);
              }}
            >
              <title>{d.node.label}</title>
              <circle cx={d.cx} cy={d.cy} r={d.r} fill={d.fill} />
              {d.label && (
                <text x={d.labelX} y={d.labelY} textAnchor="middle" className={d.strong ? "is-strong" : ""}>
                  {d.label}
                </text>
              )}
            </g>
          ))}
        </g>
      </svg>
      {highlight && (
        <div className="minimap-legend">
          <span>
            <i style={{ background: INK.cited }} />
            cited
          </span>
          <span>
            <i style={{ background: INK.related }} />
            related
          </span>
          <span>
            <i style={{ background: TYPE_FILL.document }} />
            structure
          </span>
        </div>
      )}
    </div>
  );
}
