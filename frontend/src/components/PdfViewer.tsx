import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
// Vite `?url` import so the worker ships as its own asset (SPEC §12).
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import * as api from "../api";
import type { BBox } from "../api";
import type { ReaderTarget } from "../store";
import AskAboutSelection from "./AskAboutSelection";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

/** Widest a page is printed, in CSS px — wide panes get margin, not a poster. */
const MAX_PAGE_WIDTH = 860;

interface Props {
  documentId: number;
  target: ReaderTarget | null;
  /** Page-navigator jumps; the nonce re-triggers a jump to the same page. */
  gotoPage?: { page: number; nonce: number } | null;
  onPageChange?: (page: number) => void;
}

interface HighlightBox {
  page: number;
  bbox: BBox;
}

/** Renders a PDF with pdf.js as sheets of paper: pages paint lazily to canvas
 * via IntersectionObserver, with a selectable text layer on top and
 * normalized bbox highlight overlays (SPEC §6 Reader tab). */
export default function PdfViewer({ documentId, target, gotoPage, onPageChange }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pageWrapperRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const canvasRefs = useRef<Map<number, HTMLCanvasElement>>(new Map());
  const textLayerRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const pdfRef = useRef<PDFDocumentProxy | null>(null);
  const renderedRef = useRef<Set<number>>(new Set());
  const observerRef = useRef<IntersectionObserver | null>(null);
  const onPageChangeRef = useRef(onPageChange);
  onPageChangeRef.current = onPageChange;

  const [numPages, setNumPages] = useState(0);
  const [pageDims, setPageDims] = useState<Record<number, [number, number]>>({});
  const [pageWidth, setPageWidth] = useState(MAX_PAGE_WIDTH);
  const [resolvedBoxes, setResolvedBoxes] = useState<HighlightBox[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function renderPage(pageNum: number) {
    if (renderedRef.current.has(pageNum)) return;
    const pdf = pdfRef.current;
    const canvas = canvasRefs.current.get(pageNum);
    if (!pdf || !canvas) return;
    renderedRef.current.add(pageNum);
    try {
      const page = await pdf.getPage(pageNum);
      const nativeViewport = page.getViewport({ scale: 1 });
      const scale = Math.max(0.5, Math.min(3, pageWidth / nativeViewport.width));
      const viewport = page.getViewport({ scale });
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      // Paint at device resolution so type stays crisp on retina screens.
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      await page.render({
        canvasContext: ctx,
        viewport,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
      }).promise;

      const textLayerEl = textLayerRefs.current.get(pageNum);
      if (textLayerEl) {
        // pdf.js sizes the layer and its glyph runs from these variables.
        textLayerEl.style.setProperty("--scale-factor", String(viewport.scale));
        textLayerEl.style.setProperty("--total-scale-factor", String(viewport.scale));
        textLayerEl.style.width = `${viewport.width}px`;
        textLayerEl.style.height = `${viewport.height}px`;
        textLayerEl.innerHTML = "";
        const textLayer = new pdfjsLib.TextLayer({
          textContentSource: page.streamTextContent(),
          container: textLayerEl,
          viewport,
        });
        await textLayer.render();
      }
    } catch (e) {
      renderedRef.current.delete(pageNum);
      console.error(e);
    }
  }

  // Load the document and precompute native page sizes (for stable layout /
  // accurate percent-based highlight positioning before pages are rendered).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    renderedRef.current = new Set();
    pageWrapperRefs.current = new Map();
    canvasRefs.current = new Map();
    textLayerRefs.current = new Map();
    setPageDims({});
    setNumPages(0);

    const loadingTask = pdfjsLib.getDocument({ url: api.documentFileUrl(documentId) });
    loadingTask.promise
      .then(async (pdf) => {
        if (cancelled) return;
        pdfRef.current = pdf;
        const dims: Record<number, [number, number]> = {};
        for (let i = 1; i <= pdf.numPages; i++) {
          const page = await pdf.getPage(i);
          const vp = page.getViewport({ scale: 1 });
          dims[i] = [vp.width, vp.height];
        }
        if (cancelled) return;
        setPageDims(dims);
        setNumPages(pdf.numPages);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      loadingTask.destroy();
      pdfRef.current = null;
    };
  }, [documentId]);

  // Size the sheets to the pane once it exists.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    setPageWidth(Math.max(320, Math.min(MAX_PAGE_WIDTH, el.clientWidth - 72)));
  }, [loading]);

  // Observe page wrappers and render lazily as they scroll into view.
  useEffect(() => {
    if (numPages === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const pageNum = Number((entry.target as HTMLElement).dataset.page);
            renderPage(pageNum);
          }
        }
      },
      { root: containerRef.current, rootMargin: "300px 0px" },
    );
    observerRef.current = observer;
    for (const el of pageWrapperRefs.current.values()) observer.observe(el);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [numPages, pageDims, pageWidth]);

  // Report the page under the reading line to the page navigator.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || numPages === 0) return;
    let frame = 0;
    const onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const probe = el.scrollTop + el.clientHeight * 0.3;
        let page = 1;
        for (let i = 1; i <= numPages; i++) {
          const wrapper = pageWrapperRefs.current.get(i);
          if (!wrapper) continue;
          if (wrapper.offsetTop <= probe) page = i;
          else break;
        }
        onPageChangeRef.current?.(page);
      });
    };
    onScroll();
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
  }, [numPages, pageDims]);

  // Resolve the reader target into normalized highlight boxes: use them
  // directly when provided (chat citations already carry them), otherwise
  // look up the block's bbox for a bare blockId+page ref (SPEC §6/§8).
  useEffect(() => {
    let cancelled = false;
    async function resolve() {
      if (!target) {
        setResolvedBoxes([]);
        return;
      }
      if (target.boxes?.length) {
        setResolvedBoxes(target.boxes);
        return;
      }
      if (target.blockId != null && target.page != null) {
        try {
          const blocks = await api.getBlocks(documentId, target.page);
          const block = blocks.find((b) => b.id === target.blockId);
          if (!cancelled && block?.bbox) {
            setResolvedBoxes([{ page: target.page, bbox: block.bbox }]);
            return;
          }
        } catch {
          // fall through to no highlight
        }
      }
      if (!cancelled) setResolvedBoxes([]);
    }
    resolve();
    return () => {
      cancelled = true;
    };
  }, [documentId, target]);

  function scrollToPage(page: number, boxTop?: number) {
    renderPage(page);
    const wrapper = pageWrapperRefs.current.get(page);
    const container = containerRef.current;
    if (!wrapper || !container) return;
    // Land with the marked passage in the upper third rather than the page top.
    const offset = boxTop != null ? Math.max(0, boxTop * wrapper.clientHeight - container.clientHeight * 0.3) : -24;
    container.scrollTo({ top: wrapper.offsetTop + offset, behavior: "smooth" });
  }

  // Scroll to (and force-render) the target page whenever it changes.
  useEffect(() => {
    const page = target?.page ?? resolvedBoxes[0]?.page;
    if (!page) return;
    const box = resolvedBoxes.find((b) => b.page === page);
    scrollToPage(page, box?.bbox[1]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.page, target?.nonce, numPages, resolvedBoxes]);

  useEffect(() => {
    if (gotoPage && gotoPage.page >= 1 && gotoPage.page <= numPages) scrollToPage(gotoPage.page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gotoPage?.nonce]);

  if (loading) return <p className="pane-empty">Opening the PDF…</p>;
  if (error) return <p className="pane-empty error-text">{error}</p>;

  return (
    <div className="pdf-viewer" ref={containerRef}>
      <AskAboutSelection containerRef={containerRef} />
      {Array.from({ length: numPages }, (_, i) => i + 1).map((pageNum) => {
        const dims = pageDims[pageNum];
        const boxes = resolvedBoxes.filter((b) => b.page === pageNum);
        return (
          <div
            key={pageNum}
            className="pdf-page paper"
            data-page={pageNum}
            ref={(el) => {
              if (el) {
                pageWrapperRefs.current.set(pageNum, el);
                observerRef.current?.observe(el);
              }
            }}
            style={{ width: pageWidth, aspectRatio: dims ? `${dims[0]} / ${dims[1]}` : "1 / 1.294" }}
          >
            <canvas
              ref={(el) => {
                if (el) canvasRefs.current.set(pageNum, el);
              }}
            />
            <div
              className="textLayer"
              ref={(el) => {
                if (el) textLayerRefs.current.set(pageNum, el);
              }}
            />
            {boxes.map((b, i) => (
              <div
                key={i}
                className="highlight-box"
                style={{
                  left: `${b.bbox[0] * 100}%`,
                  top: `${b.bbox[1] * 100}%`,
                  width: `${(b.bbox[2] - b.bbox[0]) * 100}%`,
                  height: `${(b.bbox[3] - b.bbox[1]) * 100}%`,
                }}
              />
            ))}
            <div className="pdf-page-number">{pageNum}</div>
          </div>
        );
      })}
    </div>
  );
}
