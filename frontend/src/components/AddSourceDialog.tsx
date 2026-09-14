import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../api";
import { useLibrary } from "../library";
import Modal from "./Modal";

export type AddMode = "upload" | "url" | "note";

export const ACCEPTED_FILES = ".pdf,.docx,.html,.htm,.md,.markdown,.txt";

interface Props {
  mode: AddMode;
  defaultNotebookId?: number;
  onClose: () => void;
}

const MODES: { key: AddMode; label: string }[] = [
  { key: "upload", label: "Upload" },
  { key: "url", label: "Web page" },
  { key: "note", label: "Note" },
];

/** The front page's "Add to a notebook": pick a notebook, then upload files,
 * add a URL or write a note; lands on that notebook's Sources. */
export default function AddSourceDialog({ mode: initialMode, defaultNotebookId, onClose }: Props) {
  const library = useLibrary();
  const navigate = useNavigate();
  const firstNotebook = library.archives.flatMap((a) => a.notebooks)[0]?.id;
  const [mode, setMode] = useState<AddMode>(initialMode);
  const [notebookId, setNotebookId] = useState<number | undefined>(defaultNotebookId ?? firstNotebook);
  const [files, setFiles] = useState<File[]>([]);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const ready =
    notebookId != null &&
    ((mode === "upload" && files.length > 0) ||
      (mode === "url" && url.trim() !== "") ||
      (mode === "note" && title.trim() !== ""));

  async function submit() {
    if (!ready || notebookId == null) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "upload") await api.uploadDocuments(notebookId, files);
      else if (mode === "url") await api.addUrlDocument(notebookId, url.trim());
      else await api.addNote(notebookId, { title: title.trim(), content_md: content });
      library.refreshArchives();
      onClose();
      navigate(`/notebook/${notebookId}`, { state: { railTab: "sources" } });
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  return (
    <Modal title="Add to a notebook" onClose={onClose} width={540}>
      <div className="seg" style={{ alignSelf: "flex-start" }}>
        {MODES.map((m) => (
          <button
            key={m.key}
            type="button"
            className={`seg-opt ${mode === m.key ? "is-on" : ""}`}
            onClick={() => setMode(m.key)}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div className="field">
        <label htmlFor="add-notebook">Notebook</label>
        <select
          id="add-notebook"
          className="input"
          value={notebookId ?? ""}
          onChange={(e) => setNotebookId(Number(e.target.value))}
        >
          {library.archives.map((a) => (
            <optgroup key={a.id} label={a.name}>
              {a.notebooks.map((nb) => (
                <option key={nb.id} value={nb.id}>
                  {nb.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      {mode === "upload" && (
        <div
          className={`dropzone ${dragOver ? "is-over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            setFiles(Array.from(e.dataTransfer.files));
          }}
        >
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => fileInputRef.current?.click()}>
            Choose files
          </button>
          <span className="hint">or drop them here — PDF, DOCX, HTML, Markdown, plain text</span>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_FILES}
            hidden
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
          {files.length > 0 && (
            <ul className="dropzone-files">
              {files.map((f) => (
                <li key={f.name}>{f.name}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {mode === "url" && (
        <div className="field">
          <label htmlFor="add-url">Web page</label>
          <input
            id="add-url"
            className="input"
            placeholder="https://…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        </div>
      )}

      {mode === "note" && (
        <>
          <div className="field">
            <label htmlFor="add-note-title">Title</label>
            <input id="add-note-title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="add-note-body">Note — Markdown</label>
            <textarea
              id="add-note-body"
              className="input"
              rows={10}
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
          </div>
        </>
      )}

      {error && <p className="error-text">{error}</p>}
      <div className="dialog-actions">
        <button type="button" className="btn btn-secondary" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="btn btn-primary" onClick={submit} disabled={!ready || busy}>
          {busy ? "Adding…" : "Add"}
        </button>
      </div>
    </Modal>
  );
}
