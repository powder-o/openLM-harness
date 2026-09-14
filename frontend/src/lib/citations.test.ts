import { describe, expect, it } from "vitest";
import type { Citation } from "../api";
import {
  citationLabel,
  citeAnchor,
  extractMarkers,
  findCitation,
  injectCitationLinks,
  parseCiteAnchor,
} from "./citations";

describe("injectCitationLinks", () => {
  it("wraps chunk and figure markers as markdown links to a cite anchor", () => {
    const input = "Entropy is defined in [c:12] and shown in [f:45].";
    const out = injectCitationLinks(input);
    expect(out).toBe(
      "Entropy is defined in [[c:12]](#cite-c-12) and shown in [[f:45]](#cite-f-45).",
    );
  });

  it("leaves text with no markers untouched", () => {
    expect(injectCitationLinks("no markers here")).toBe("no markers here");
  });

  it("round-trips through parseCiteAnchor", () => {
    const anchor = citeAnchor("c", 7);
    expect(parseCiteAnchor(anchor)).toEqual({ kind: "c", id: 7, marker: "c:7" });
  });

  it("returns null for an unrelated href", () => {
    expect(parseCiteAnchor("https://example.com")).toBeNull();
  });
});

describe("extractMarkers", () => {
  it("dedupes repeated markers and preserves first-seen order", () => {
    const markers = extractMarkers("[c:1] text [f:2] more [c:1] again");
    expect(markers).toEqual([
      { kind: "c", id: 1, marker: "c:1" },
      { kind: "f", id: 2, marker: "f:2" },
    ]);
  });
});

const chunkCitation: Citation = {
  marker: "c:12",
  kind: "chunk",
  chunk_id: 12,
  document_id: 3,
  document_title: "Thermodynamics Notes",
  document_kind: "pdf",
  page: 4,
  heading_path: "Ch1 > Entropy",
  snippet: "Entropy is a measure of disorder...",
  boxes: [{ page: 4, bbox: [0.1, 0.1, 0.5, 0.2] }],
};

const nonPagedChunkCitation: Citation = {
  ...chunkCitation,
  marker: "c:13",
  chunk_id: 13,
  document_kind: "md",
  page: null,
  boxes: [],
};

describe("findCitation / citationLabel", () => {
  it("finds a citation by marker", () => {
    expect(findCitation([chunkCitation], "c:12")).toBe(chunkCitation);
    expect(findCitation([chunkCitation], "c:999")).toBeUndefined();
    expect(findCitation(undefined, "c:12")).toBeUndefined();
  });

  it("labels a paged chunk citation with its page number", () => {
    expect(citationLabel(chunkCitation)).toBe("p.4");
  });

  it("labels a non-paged chunk citation with the document title", () => {
    expect(citationLabel(nonPagedChunkCitation)).toBe("Thermodynamics Notes");
  });

  it("labels a figure citation with its label, falling back to document title", () => {
    const figure: Citation = {
      marker: "f:45",
      kind: "figure",
      block_id: 45,
      document_id: 3,
      document_title: "Thermodynamics Notes",
      label: "Figure 3.2",
      caption: "Entropy over time",
      image_url: "/api/blocks/45/image",
      page: 4,
      bbox: [0.1, 0.1, 0.5, 0.2],
    };
    expect(citationLabel(figure)).toBe("Figure 3.2");
    expect(citationLabel({ ...figure, label: null })).toBe("Thermodynamics Notes");
  });
});
