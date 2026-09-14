import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage, Citation, ToolCallRecord } from "../api";
import {
  CITATION_MARKER_RE,
  citationLabel,
  findCitation,
  injectCitationLinks,
  parseCiteAnchor,
} from "../lib/citations";
import { clip } from "../lib/format";
import { openCitation } from "../lib/reader";
import { useStore } from "../store";
import ToolSummary from "./ToolSummary";

/** Pills stay short: "p.144", or the first part of a page-less source's title. */
function pillLabel(citation: Citation): string {
  const label = citationLabel(citation);
  if (citation.kind !== "chunk" || citation.page != null) return label;
  return clip(label.split(/\s+[—–:|]\s+/)[0], 26);
}

/** A question set as the page's pull quote. Text quoted from the Reader
 * ("> …" lines) is shown above it as a plain blockquote. */
export function Question({ content }: { content: string }) {
  const lines = content.split("\n");
  const quote = lines
    .filter((l) => l.startsWith(">"))
    .map((l) => l.replace(/^>\s?/, ""))
    .join("\n")
    .trim();
  const question = lines
    .filter((l) => !l.startsWith(">"))
    .join("\n")
    .trim();
  return (
    <div className="turn turn-question">
      <div className="kicker kicker-cyan">You asked</div>
      {quote && <blockquote className="question-quote">{quote}</blockquote>}
      {question && <p className="question">{question}</p>}
    </div>
  );
}

/** The live turn while the agent works: its tool line, then the text as it streams. */
export function StreamingAnswer({ content, calls }: { content: string; calls: ToolCallRecord[] }) {
  return (
    <div className="turn turn-answer">
      <ToolSummary calls={calls} />
      {content ? (
        <div className="prose">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content.replace(CITATION_MARKER_RE, "")}</ReactMarkdown>
        </div>
      ) : (
        <div className="searching">
          <span className="dot dot-cyan dot-pulse" />
          {calls.length ? "Reading your sources…" : "Searching your sources…"}
        </div>
      )}
    </div>
  );
}

interface Props {
  message: ChatMessage;
}

export default function Message({ message }: Props) {
  const store = useStore();

  if (message.role === "user") return <Question content={message.content} />;

  const origin = store.readerTarget?.origin;
  const openMarker = store.pane === "reader" && origin?.messageId === message.id ? origin.marker : null;

  const open = (citation: Citation) => {
    openCitation(citation, (target) =>
      store.openReader({
        ...target,
        origin: {
          messageId: message.id,
          marker: citation.marker,
          label: citationLabel(citation),
          heading: citation.kind === "chunk" ? citation.heading_path : citation.label,
        },
      }),
    ).catch((e) => console.error(e));
  };

  const components: Components = {
    a: ({ href, children }) => {
      const parsed = href ? parseCiteAnchor(href) : null;
      if (!parsed) {
        return (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        );
      }
      const citation = findCitation(message.citations, parsed.marker);
      // Markers without a matching citation render as nothing (SPEC §12).
      if (!citation) return null;

      if (citation.kind === "figure") {
        return (
          <span
            className={`figure-cite ${openMarker === citation.marker ? "is-open" : ""}`}
            role="button"
            tabIndex={0}
            onClick={() => open(citation)}
            onKeyDown={(e) => e.key === "Enter" && open(citation)}
          >
            {citation.image_url && (
              <span className="figure-cite-image">
                <img src={citation.image_url} alt={citation.label || "figure"} />
              </span>
            )}
            <span className="figure-cite-caption">
              <strong>{citation.label || "Figure"}</strong>
              {citation.caption ? ` — ${citation.caption}` : ""} {citation.document_title}
              {citation.page != null ? `, p.${citation.page}` : ""}.
            </span>
          </span>
        );
      }
      return (
        <button
          type="button"
          className={`cite ${openMarker === citation.marker ? "is-open" : ""}`}
          title={`${citation.document_title}${citation.heading_path ? ` — ${citation.heading_path}` : ""}`}
          onClick={() => open(citation)}
        >
          {pillLabel(citation)}
        </button>
      );
    },
  };

  return (
    <div className="turn turn-answer">
      <ToolSummary calls={message.tool_calls || []} citations={message.citations} />
      <div className="prose">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
          {injectCitationLinks(message.content)}
        </ReactMarkdown>
      </div>
    </div>
  );
}
