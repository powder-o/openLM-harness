import { useRef, useState } from "react";
import type { DragEvent } from "react";
import * as api from "../api";
import type { DocumentSummary } from "../api";
import { count, kindMark, kindName } from "../lib/format";
import { useStore } from "../store";
import { ACCEPTED_FILES } from "./AddSourceDialog";
import Modal from "./Modal";

interface Props {
  notebookId: number;
}

const STATUS_WORDS: Record<string, string> = {
  queued: "Queued",
  parsing: "Parsing layout",
  describing: "Describing figures",
  indexing: "Indexing passages",
  graphing: "Building the map",
};

/** The Sources tab: add files, pages and notes; watch them ingest; open,
 * edit, re-ingest or delete them. Polling lives in the store. */
export default function SourcesPanel({ notebookId }: Props) {
  const store = useStore();
  const { documents, documentsLoaded, refreshDocuments } = store;
  const [uploading, setUploading] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [noteOpen, setNoteOpen] = useState(false);
  const [noteEditingId, setNoteEditingId] = useState<number | null>(null);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteContent, setNoteContent] = useState("");

  async function attempt(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      refreshDocuments();
      return true;
    } catch (e) {
      setError(String(e));
      return false;
    }
  }

  async function handleUpload(files: File[]) {
    if (!files.length) return;
    setUploading(true);
    await attempt(() => api.uploadDocuments(notebookId, files));
    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    handleUpload(Array.from(e.dataTransfer.files));
  }

  async function handleAddUrl() {
    const url = urlInput.trim();
    if (!url) return;
    if (await attempt(() => api.addUrlDocument(notebookId, url))) setUrlInput("");
  }

  function handleDelete(doc: DocumentSummary) {
    if (!confirm(`Delete “${doc.title}”? Its passages, figures and map nodes go with it.`)) return;
    attempt(() => api.deleteDocument(doc.id));
  }

  function openNewNote() {
    setNoteEditingId(null);
    setNoteTitle("");
    setNoteContent("");
    setNoteOpen(true);
  }

  async function openEditNote(doc: DocumentSummary) {
    try {
      const note = await api.getNote(doc.id);
      setNoteEditingId(doc.id);
      setNoteTitle(note.title);
      setNoteContent(note.content_md);
      setNoteOpen(true);
    } catch (e) {
      setError(String(e));
    }
  }

  async function saveNote() {
    const ok = await attempt(() =>
      noteEditingId == null
        ? api.addNote(notebookId, { title: noteTitle, content_md: noteContent })
        : api.updateNote(noteEditingId, { title: noteTitle, content_md: noteContent }),
    );
    if (ok) setNoteOpen(false);
  }

  return (
    <div className="side-panel">
      <section className="side-section">
        <div
          className={`dropzone ${dragOver ? "is-over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? "Uploading…" : "Upload sources"}
          </button>
          <span className="hint">or drop PDF, DOCX, HTML, Markdown or text here</span>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_FILES}
            hidden
            onChange={(e) => handleUpload(Array.from(e.target.files ?? []))}
          />
        </div>
        <div className="inline-form">
          <input
            className="input input-sm"
            placeholder="https://… a web page"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAddUrl()}
          />
          <button type="button" className="btn btn-secondary btn-sm" onClick={handleAddUrl} disabled={!urlInput.trim()}>
            Add
          </button>
        </div>
        <button type="button" className="link side-inline-link" onClick={openNewNote}>
          Write a note
        </button>
        {error && <p className="error-text">{error}</p>}
      </section>

      <section className="side-section">
        <div className="kicker">Sources · {documents.length}</div>
        {documentsLoaded && documents.length === 0 && (
          <p className="hint">
            Nothing here yet. Every source is parsed with its pages and positions kept, so answers can point back at
            the exact spot.
          </p>
        )}
        {documents.map((d) => {
          const active = d.status !== "ready" && d.status !== "error";
          return (
            <div key={d.id} className="source-row">
              <span className="thumb thumb-kind">{kindMark(d.kind)}</span>
              <div className="source-row-text">
                {d.status === "ready" ? (
                  <button
                    type="button"
                    className="source-row-title"
                    onClick={() => store.openReader({ documentId: d.id })}
                  >
                    {d.title}
                  </button>
                ) : (
                  <span className="source-row-title">{d.title}</span>
                )}
                <div className="source-row-meta">
                  {active ? (
                    <span className="status-live">
                      <span className="dot dot-yellow dot-pulse" />
                      {d.status_detail || STATUS_WORDS[d.status] || d.status}
                    </span>
                  ) : d.status === "error" ? (
                    <span className="error-text">Failed{d.error ? ` — ${d.error}` : ""}</span>
                  ) : (
                    <span>
                      {kindName(d.kind)}
                      {d.num_pages ? ` · ${count(d.num_pages, "page")}` : ""}
                    </span>
                  )}
                </div>
                <div className="source-row-actions">
                  {d.status === "ready" && (
                    <button type="button" className="link" onClick={() => store.openReader({ documentId: d.id })}>
                      Open
                    </button>
                  )}
                  {d.kind === "note" && (
                    <button type="button" className="link link-quiet" onClick={() => openEditNote(d)}>
                      Edit
                    </button>
                  )}
                  <button type="button" className="link link-quiet" onClick={() => attempt(() => api.reingestDocument(d.id))}>
                    Re-ingest
                  </button>
                  <button type="button" className="link link-quiet" onClick={() => handleDelete(d)}>
                    Delete
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </section>

      {noteOpen && (
        <Modal title={noteEditingId == null ? "Write a note" : "Edit note"} onClose={() => setNoteOpen(false)} width={600}>
          <div className="field">
            <label htmlFor="note-title">Title</label>
            <input id="note-title" className="input" value={noteTitle} onChange={(e) => setNoteTitle(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="note-body">Note — Markdown</label>
            <textarea
              id="note-body"
              className="input"
              rows={14}
              value={noteContent}
              onChange={(e) => setNoteContent(e.target.value)}
            />
          </div>
          <div className="dialog-actions">
            <button type="button" className="btn btn-secondary" onClick={() => setNoteOpen(false)}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={saveNote} disabled={!noteTitle.trim()}>
              Save
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
