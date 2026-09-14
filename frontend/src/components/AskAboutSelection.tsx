import { useEffect, useState } from "react";
import type { RefObject } from "react";
import { useStore } from "../store";

interface Props {
  containerRef: RefObject<HTMLElement>;
}

/** Floating "Ask about this · Copy" that appears over a text selection inside
 * a Reader container; asking quotes the selection into the composer (SPEC §6). */
export default function AskAboutSelection({ containerRef }: Props) {
  const store = useStore();
  const [pos, setPos] = useState<{ x: number; y: number; text: string } | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    function onMouseUp() {
      const sel = window.getSelection();
      const text = sel?.toString().trim() ?? "";
      if (!container || !text || !sel || sel.rangeCount === 0) {
        setPos(null);
        return;
      }
      const range = sel.getRangeAt(0);
      if (!container.contains(range.commonAncestorContainer)) {
        setPos(null);
        return;
      }
      const rect = range.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();
      // The popover is positioned inside the scrolling container, so offsets
      // include its scroll position.
      setPos({
        x: rect.left - containerRect.left + rect.width / 2 + container.scrollLeft,
        y: rect.top - containerRect.top + container.scrollTop,
        text,
      });
    }

    container.addEventListener("mouseup", onMouseUp);
    return () => container.removeEventListener("mouseup", onMouseUp);
  }, [containerRef]);

  if (!pos) return null;

  function done() {
    setPos(null);
    window.getSelection()?.removeAllRanges();
  }

  return (
    <div
      className="ask-popover"
      style={{ left: pos.x, top: Math.max(40, pos.y - 8) }}
      onMouseDown={(e) => e.preventDefault()}
    >
      <button
        type="button"
        className="ask-popover-main"
        onClick={() => {
          const quoted = pos.text
            .split("\n")
            .map((l) => `> ${l}`)
            .join("\n");
          store.setComposerDraft(`${store.composerDraft}${store.composerDraft ? "\n\n" : ""}${quoted}\n\n`);
          done();
        }}
      >
        Ask about this
      </button>
      <span className="ask-popover-sep">·</span>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(pos.text).catch(() => {});
          done();
        }}
      >
        Copy
      </button>
    </div>
  );
}
