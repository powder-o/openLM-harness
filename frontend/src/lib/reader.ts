// Shared helpers for turning a citation / chunk id / graph node ref into a
// ReaderTargetInput and pushing it into the store. Used by the chat message
// citation pills, the Study tab's "source chunk" links, and GraphView's node
// click handling, so the "open X in the Reader" behaviour is defined once.
import * as api from "../api";
import type { Citation } from "../api";
import type { ReaderTargetInput } from "../store";

/** A chunk's own blocks carry page+bbox for paged (PDF) documents; for
 * non-paged documents (md/txt/html/web/docx/note) there is no bbox, so we
 * just scroll to the chunk's first block (SPEC §11: "non-PDF documents:
 * GET /api/chunks/{id} and scroll to its first block"). */
export function chunkToReaderTarget(chunk: api.ChunkDetail): ReaderTargetInput {
  const boxes = chunk.blocks
    .filter((b): b is api.ChunkBlockRef & { page: number; bbox: api.BBox } => b.page != null && b.bbox != null)
    .map((b) => ({ page: b.page, bbox: b.bbox }));
  return {
    documentId: chunk.document_id,
    page: chunk.page_start ?? undefined,
    blockId: chunk.blocks[0]?.id,
    boxes: boxes.length ? boxes : undefined,
  };
}

export async function openChunkById(
  chunkId: number,
  openReader: (target: ReaderTargetInput) => void,
): Promise<void> {
  const chunk = await api.getChunk(chunkId);
  openReader(chunkToReaderTarget(chunk));
}

/** Citation objects from a chat message already carry boxes/page for paged
 * documents, so we can open the Reader without a round trip; for non-paged
 * (chunk) citations we don't have a block id and must fetch the chunk. Figure
 * citations always carry their own block id. */
export async function openCitation(
  citation: Citation,
  openReader: (target: ReaderTargetInput) => void,
): Promise<void> {
  if (citation.kind === "figure") {
    openReader({
      documentId: citation.document_id,
      blockId: citation.block_id,
      page: citation.page ?? undefined,
      boxes:
        citation.page != null && citation.bbox != null
          ? [{ page: citation.page, bbox: citation.bbox }]
          : undefined,
    });
    return;
  }
  if (citation.document_kind === "pdf" || citation.boxes.length) {
    openReader({
      documentId: citation.document_id,
      page: citation.page ?? undefined,
      boxes: citation.boxes.length ? citation.boxes : undefined,
    });
    return;
  }
  await openChunkById(citation.chunk_id, openReader);
}

export function openDocumentRef(
  openReader: (target: ReaderTargetInput) => void,
  ref: { document_id: number; block_id?: number; page?: number },
): void {
  openReader({ documentId: ref.document_id, blockId: ref.block_id, page: ref.page });
}
