import type { ReactNode } from "react";
import type { Citation, FigureCitation, ToolCallRecord } from "../api";
import { count } from "../lib/format";

const KNOWN_TOOLS = new Set(["search", "read_context", "get_figure", "list_documents"]);

function clip(text: string, max = 48): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

interface Props {
  calls: ToolCallRecord[];
  citations?: Citation[];
}

/** One quiet line of what the agent did, e.g. "Searched canonical ensemble ·
 * Boltzmann — read 4 passages from 2 sources, opened Figure 3.2". Shared by
 * finished answers and the live streaming turn. Hover shows each tool call. */
export default function ToolSummary({ calls, citations = [] }: Props) {
  if (!calls.length) return null;

  const queries = [
    ...new Set(
      calls
        .filter((c) => c.name === "search")
        .map((c) => c.arguments?.query)
        .filter((q): q is string => typeof q === "string" && q.trim() !== ""),
    ),
  ];
  const reads = calls.filter((c) => c.name === "read_context").length;
  const chunkCitations = citations.filter((c) => c.kind === "chunk");
  const passages = chunkCitations.length || reads;
  const sources = new Set(chunkCitations.map((c) => c.document_id)).size;
  const figureLabels = citations
    .filter((c): c is FigureCitation => c.kind === "figure")
    .map((c) => c.label || "a figure");
  const figureCalls = calls.filter((c) => c.name === "get_figure").length;
  const listed = calls.some((c) => c.name === "list_documents");
  const other = [...new Set(calls.filter((c) => !KNOWN_TOOLS.has(c.name)).map((c) => c.name.replace(/_/g, " ")))];

  const parts: ReactNode[] = [];
  if (queries.length) {
    parts.push(
      <>
        searched <span className="muted">{queries.map((q) => clip(q)).join(" · ")}</span>
      </>,
    );
  }
  if (listed) parts.push("listed the sources");
  if (passages) {
    parts.push(`read ${count(passages, "passage")}${sources ? ` from ${count(sources, "source")}` : ""}`);
  }
  if (figureLabels.length) {
    parts.push(
      <>
        opened <span className="muted">{figureLabels.join(", ")}</span>
      </>,
    );
  } else if (figureCalls) {
    parts.push(`opened ${count(figureCalls, "figure")}`);
  }
  if (other.length) parts.push(`used ${other.join(", ")}`);
  if (!parts.length) return null;

  const [head, ...rest] = parts;
  return (
    <div
      className="tool-summary"
      title={calls.map((c) => (c.summary ? `${c.name}: ${c.summary}` : c.name)).join("\n")}
    >
      <span className="tool-summary-head">{head}</span>
      {rest.length > 0 && (
        <>
          {" — "}
          {rest.map((p, i) => (
            <span key={i}>
              {i > 0 ? ", " : ""}
              {p}
            </span>
          ))}
        </>
      )}
    </div>
  );
}
