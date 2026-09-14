import { useEffect, useState } from "react";
import * as api from "../api";
import type { DocumentDetail } from "../api";
import { count, kindName, lastHeading } from "../lib/format";
import { useStore } from "../store";
import BlockReader from "./BlockReader";
import PdfViewer from "./PdfViewer";

/** The Reader pane. Opening a citation puts it in charge: the page prints on
 * paper with the cited passage marked in process yellow (SPEC §6 Reader). */
export default function ReaderPanel() {
  const store = useStore();
  const target = store.readerTarget;
  const [docId, setDocId] = useState<number | null>(target?.documentId ?? null);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"page" | "blocks">("page");
  const [currentPage, setCurrentPage] = useState<number | null>(null);
  const [gotoPage, setGotoPage] = useState<{ page: number; nonce: number } | null>(null);
  const { closePane, documents } = store;

  useEffect(() => {
    if (target) setDocId(target.documentId);
  }, [target]);

  useEffect(() => {
    if (docId == null && documents.length) setDocId(documents[0].id);
  }, [docId, documents]);

  useEffect(() => {
    if (docId == null) return;
    let cancelled = false;
    setDetail(null);
    setError(null);
    setCurrentPage(null);
    setView("page");
    api
      .getDocument(docId)
      .then((d) => !cancelled && setDetail(d))
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [docId]);

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

  const activeTarget = target && target.documentId === docId ? target : null;
  const origin = activeTarget?.origin;
  const heading = lastHeading(origin?.heading, 44);
  const isPdf = detail?.kind === "pdf";
  const pages = detail?.num_pages ?? null;

  return (
    <section className="pane" aria-label="Reader">
      <header className="pane-head">
        <div className="pane-head-main">
          <div className={`kicker ${origin ? "kicker-yellow" : ""}`}>
            {origin ? `Opened from your answer · ${origin.label}` : "Reader"}
          </div>
          <div className="pane-title">
            {documents.length > 1 ? (
              <select
                className="pane-title-select"
                aria-label="Document"
                value={docId ?? ""}
                onChange={(e) => setDocId(Number(e.target.value))}
              >
                {documents.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title}
                  </option>
                ))}
              </select>
            ) : (
              <span className="pane-title-text">{detail?.title ?? ""}</span>
            )}
            {detail && (
              <span className="pane-title-meta">
                {pages ? ` · ${count(pages, "page")}` : ""} · {kindName(detail.kind)}
              </span>
            )}
          </div>
        </div>
        <div className="pane-tools">
          {heading && <span className="pane-section" title={origin?.heading ?? undefined}>§ {heading}</span>}
          {isPdf && view === "page" && pages ? (
            <PageNav
              current={currentPage}
              total={pages}
              onGo={(page) => setGotoPage((g) => ({ page, nonce: (g?.nonce ?? 0) + 1 }))}
            />
          ) : null}
          {isPdf && (
            <button type="button" className="link" onClick={() => setView((v) => (v === "page" ? "blocks" : "page"))}>
              {view === "page" ? "Blocks view" : "Page view"}
            </button>
          )}
          <button type="button" className="btn btn-secondary btn-sm" onClick={closePane} title="Close the reader (Esc)">
            Close
          </button>
        </div>
      </header>

      <div className="pane-stage">
        {error && <p className="pane-empty error-text">{error}</p>}
        {docId == null && !error && <p className="pane-empty">Add a source to read it here.</p>}
        {docId != null && !detail && !error && <p className="pane-empty">Opening…</p>}
        {detail && isPdf && view === "page" && (
          <PdfViewer
            key={detail.id}
            documentId={detail.id}
            target={activeTarget}
            gotoPage={gotoPage}
            onPageChange={setCurrentPage}
          />
        )}
        {detail && (!isPdf || view === "blocks") && (
          <BlockReader key={`blocks-${detail.id}`} documentId={detail.id} target={activeTarget} />
        )}
      </div>
    </section>
  );
}

function PageNav({ current, total, onGo }: { current: number | null; total: number; onGo: (page: number) => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const page = current ?? 1;

  return (
    <span className="page-nav">
      <button type="button" aria-label="Previous page" disabled={page <= 1} onClick={() => onGo(page - 1)}>
        ◂
      </button>
      <input
        aria-label="Page"
        inputMode="numeric"
        value={editing ? text : String(page)}
        style={{ width: `${String(total).length + 0.6}ch` }}
        onFocus={() => {
          setEditing(true);
          setText(String(page));
        }}
        onBlur={() => setEditing(false)}
        onChange={(e) => setText(e.target.value.replace(/\D/g, ""))}
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          const n = Number(text);
          if (n >= 1 && n <= total) onGo(n);
          (e.target as HTMLInputElement).blur();
        }}
      />
      <span className="faint">/ {total}</span>
      <button type="button" aria-label="Next page" disabled={page >= total} onClick={() => onGo(page + 1)}>
        ▸
      </button>
    </span>
  );
}
