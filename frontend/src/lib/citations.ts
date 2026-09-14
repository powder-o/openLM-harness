// Parses `[c:N]` (chunk) and `[f:N]` (figure/table) citation markers that the
// chat agent emits inline in assistant markdown (SPEC §11). Markers are turned
// into markdown links pointing at a synthetic `#cite-<kind>-<id>` anchor so
// react-markdown parses them normally; the Message component's custom `a`
// renderer then swaps those anchors for citation pills / figure cards, looked
// up by marker in the message's `citations` array. A marker with no matching
// citation renders as nothing (SPEC: "Markers without a matching citation
// render as nothing").

import type { Citation } from "../api";

export const CITATION_MARKER_RE = /\[(c|f):(\d+)\]/g;

export type MarkerKind = "c" | "f";

export interface ParsedMarker {
  kind: MarkerKind;
  id: number;
  marker: string; // e.g. "c:12"
}

export function citeAnchor(kind: MarkerKind, id: number): string {
  return `#cite-${kind}-${id}`;
}

export function parseCiteAnchor(href: string): ParsedMarker | null {
  const m = /^#cite-(c|f)-(\d+)$/.exec(href);
  if (!m) return null;
  const kind = m[1] as MarkerKind;
  const id = Number(m[2]);
  return { kind, id, marker: `${kind}:${id}` };
}

/** Replace `[c:12]` / `[f:45]` markers with markdown links so react-markdown
 * renders them as `<a>` tags we can intercept. Leaves everything else as-is. */
export function injectCitationLinks(content: string): string {
  return content.replace(CITATION_MARKER_RE, (full, kind: MarkerKind, idStr: string) => {
    const id = Number(idStr);
    return `[${full}](${citeAnchor(kind, id)})`;
  });
}

export function findCitation(
  citations: Citation[] | undefined,
  marker: string,
): Citation | undefined {
  return citations?.find((c) => c.marker === marker);
}

/** Label shown on a chunk citation pill: "p.X" when the source doc is paged,
 * otherwise the document title (SPEC: label "p.X" or the document title for
 * non-paged docs). */
export function citationLabel(citation: Citation): string {
  if (citation.kind === "chunk") {
    return citation.page != null ? `p.${citation.page}` : citation.document_title;
  }
  return citation.label || citation.document_title;
}

/** Extract every marker referenced in a message's content, in order, deduped. */
export function extractMarkers(content: string): ParsedMarker[] {
  const out: ParsedMarker[] = [];
  const seen = new Set<string>();
  for (const m of content.matchAll(CITATION_MARKER_RE)) {
    const kind = m[1] as MarkerKind;
    const id = Number(m[2]);
    const marker = `${kind}:${id}`;
    if (!seen.has(marker)) {
      seen.add(marker);
      out.push({ kind, id, marker });
    }
  }
  return out;
}
