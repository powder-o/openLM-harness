import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import type { Block } from "../api";
import type { ReaderTarget } from "../store";
import AskAboutSelection from "./AskAboutSelection";

interface Props {
  documentId: number;
  target: ReaderTarget | null;
}

type Group = { type: "list"; blocks: Block[] } | { type: "single"; block: Block };

function groupBlocks(blocks: Block[]): Group[] {
  const groups: Group[] = [];
  for (const b of blocks) {
    if (b.type === "list_item") {
      const last = groups[groups.length - 1];
      if (last && last.type === "list") last.blocks.push(b);
      else groups.push({ type: "list", blocks: [b] });
    } else {
      groups.push({ type: "single", block: b });
    }
  }
  return groups;
}

function BlockItem({ block, highlighted }: { block: Block; highlighted: boolean }) {
  const id = `block-${block.id}`;
  const cls = `reader-block ${highlighted ? "highlighted" : ""}`;
  switch (block.type) {
    case "heading": {
      const level = Math.min(Math.max(block.level ?? 1, 1), 6);
      const Tag = `h${level}` as keyof JSX.IntrinsicElements;
      return (
        <Tag id={id} className={cls}>
          {block.text}
        </Tag>
      );
    }
    case "list_item":
      return (
        <li id={id} className={cls}>
          {block.text}
        </li>
      );
    case "caption":
      return (
        <p id={id} className={`${cls} block-caption`}>
          {block.text}
        </p>
      );
    case "figure":
      return (
        <figure id={id} className={`${cls} block-figure`}>
          {block.image_url && <img src={block.image_url} alt={block.caption || block.label || "figure"} />}
          <figcaption>
            {block.label ? <strong>{block.label}. </strong> : null}
            {block.caption || block.description}
          </figcaption>
        </figure>
      );
    case "table":
      return (
        <div id={id} className={`${cls} block-table`}>
          {block.label && <p className="block-caption">{block.label}</p>}
          {block.table_html ? (
            <div dangerouslySetInnerHTML={{ __html: block.table_html }} />
          ) : (
            <pre>{block.text}</pre>
          )}
          {block.caption && <p className="block-caption">{block.caption}</p>}
        </div>
      );
    case "equation":
      return (
        <pre id={id} className={`${cls} block-equation`}>
          {block.text}
        </pre>
      );
    case "code":
      return (
        <pre id={id} className={`${cls} block-code`}>
          <code>{block.text}</code>
        </pre>
      );
    case "footnote":
      return (
        <p id={id} className={`${cls} block-footnote`}>
          {block.text}
        </p>
      );
    default:
      return (
        <p id={id} className={cls}>
          {block.text}
        </p>
      );
  }
}

/** Renders a document from its canonical blocks, set on a sheet of paper —
 * the reader for non-PDF sources and the "Blocks view" of a PDF
 * (SPEC §6 Reader tab, §13 GET /api/documents/{id}/blocks). */
export default function BlockReader({ documentId, target }: Props) {
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [loading, setLoading] = useState(true);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setLoading(true);
    api.getBlocks(documentId).then((b) => {
      setBlocks([...b].sort((a, c) => a.seq - c.seq));
      setLoading(false);
    });
  }, [documentId]);

  useEffect(() => {
    if (!target?.blockId) return;
    const el = document.getElementById(`block-${target.blockId}`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [target?.blockId, target?.nonce, blocks]);

  const groups = useMemo(() => groupBlocks(blocks), [blocks]);

  if (loading) return <p className="pane-empty">Loading…</p>;
  if (blocks.length === 0) return <p className="pane-empty">This document has no content yet.</p>;

  return (
    <div className="block-reader" ref={containerRef}>
      <AskAboutSelection containerRef={containerRef} />
      <article className="paper paper-flow">
        {groups.map((g, i) =>
          g.type === "list" ? (
            <ul key={i} className="block-list">
              {g.blocks.map((b) => (
                <BlockItem key={b.id} block={b} highlighted={b.id === target?.blockId} />
              ))}
            </ul>
          ) : (
            <BlockItem key={g.block.id} block={g.block} highlighted={g.block.id === target?.blockId} />
          ),
        )}
      </article>
    </div>
  );
}
