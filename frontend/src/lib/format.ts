// Small wording helpers shared by the front page, headers and rails.
import type { ChatSession } from "../api";

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"];

/** "1 source", "12 sources". */
export function count(n: number, noun: string, plural = `${noun}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? noun : plural}`;
}

/** "Three notebooks" — small counts spelled out, as a newspaper would. */
export function countWord(n: number, noun: string, plural = `${noun}s`): string {
  return `${n < WORDS.length ? WORDS[n] : n.toLocaleString()} ${n === 1 ? noun : plural}`;
}

/** "today", "yesterday", "3 days ago", "4 Sep". */
export function relativeDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(new Date()) - startOf(d)) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

const KIND_MARKS: Record<string, string> = {
  pdf: "PDF",
  md: "MD",
  markdown: "MD",
  txt: "TXT",
  text: "TXT",
  html: "WEB",
  web: "WEB",
  url: "WEB",
  docx: "DOC",
  note: "NOTE",
};

const KIND_NAMES: Record<string, string> = {
  pdf: "PDF",
  md: "Markdown",
  markdown: "Markdown",
  txt: "Text",
  text: "Text",
  html: "Web page",
  web: "Web page",
  url: "Web page",
  docx: "Word",
  note: "Note",
};

/** Short mark for a thumbnail, e.g. "MD". */
export function kindMark(kind: string): string {
  return KIND_MARKS[kind] ?? kind.slice(0, 4).toUpperCase();
}

/** Reader-facing name, e.g. "Web page". */
export function kindName(kind: string): string {
  return KIND_NAMES[kind] ?? kind;
}

/** Last segment of a heading path ("Ch. 3 > 3.3 The canonical distribution"). */
export function lastHeading(path: string | null | undefined, max = 36): string | null {
  if (!path) return null;
  const parts = path.split(/\s*(?:>|›|»|\||\s\/\s)\s*/).filter(Boolean);
  const last = parts[parts.length - 1];
  return last ? clip(last, max) : null;
}

export function threadTitle(session: ChatSession): string {
  if (session.title && session.title !== "New chat") return session.title;
  return new Date(session.created_at).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}
