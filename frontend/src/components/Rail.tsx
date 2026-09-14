import { Fragment, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import * as api from "../api";
import { initials, statusLine, useLibrary } from "../library";

interface Props {
  /** Folded to initials while a Reader/Map pane is in charge. */
  folded?: boolean;
  /** Context sections for the current screen (a notebook's threads, etc.). */
  children?: ReactNode;
}

type Editing =
  | { kind: "new-archive" }
  | { kind: "new-notebook"; archiveId: number }
  | { kind: "rename-archive"; id: number; name: string }
  | { kind: "rename-notebook"; id: number; name: string };

function InlineName({
  initial = "",
  placeholder,
  onSubmit,
  onCancel,
}: {
  initial?: string;
  placeholder: string;
  onSubmit: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement | null>(null);
  const done = useRef(false);

  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  function finish(submit: boolean) {
    if (done.current) return;
    done.current = true;
    const name = value.trim();
    if (submit && name && name !== initial) onSubmit(name);
    else onCancel();
  }

  return (
    <div className="rail-edit">
      <input
        ref={ref}
        className="input"
        value={value}
        placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => finish(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter") finish(true);
          if (e.key === "Escape") finish(false);
        }}
      />
    </div>
  );
}

/** The left rail: archives as small-caps heads with their notebooks beneath,
 * any screen-specific sections, and the chat agent's status at the foot. */
export default function Rail({ folded = false, children }: Props) {
  const library = useLibrary();
  const navigate = useNavigate();
  const location = useLocation();
  const [editing, setEditing] = useState<Editing | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const notebookMatch = /^\/notebook\/(\d+)/.exec(location.pathname);
  const archiveMatch = /^\/archive\/(\d+)/.exec(location.pathname);
  const activeNotebookId = notebookMatch ? Number(notebookMatch[1]) : null;
  const activeArchiveId = archiveMatch
    ? Number(archiveMatch[1])
    : library.archives.find((a) => a.notebooks.some((n) => n.id === activeNotebookId))?.id ?? null;
  const status = statusLine(library.chatStatus);

  async function run(action: () => Promise<unknown>) {
    setEditing(null);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(String(e));
    }
    library.refreshArchives();
  }

  async function handleDeleteArchive(id: number, name: string) {
    if (!confirm(`Delete archive "${name}" and all its notebooks?`)) return;
    await run(() => api.deleteArchive(id));
    if (activeArchiveId === id) navigate("/");
  }

  async function handleDeleteNotebook(id: number, name: string) {
    if (!confirm(`Delete notebook "${name}" and all its sources?`)) return;
    await run(() => api.deleteNotebook(id));
    if (activeNotebookId === id) navigate("/");
  }

  if (folded) {
    return (
      <nav className="rail is-folded" aria-label="Library">
        <Link to="/" className="rail-mark-brand" title="Study">
          S
        </Link>
        <div className="rail-divider" />
        <div className="rail-marks">
          {library.archives.map((a, i) => (
            <Fragment key={a.id}>
              {i > 0 && a.notebooks.length > 0 && <div className="rail-mark-gap" />}
              {a.notebooks.map((nb) => (
                <Link
                  key={nb.id}
                  to={`/notebook/${nb.id}`}
                  className={`rail-mark ${activeNotebookId === nb.id ? "is-active" : ""}`}
                  title={`${a.name} · ${nb.name}`}
                >
                  {initials(nb.name)}
                </Link>
              ))}
            </Fragment>
          ))}
        </div>
        <button
          type="button"
          className="rail-status-button"
          title={`${status.text} — Settings`}
          onClick={() => library.setSettingsOpen(true)}
        >
          <span className={`dot ${status.ok ? "dot-cyan" : "dot-magenta"}`} />
        </button>
      </nav>
    );
  }

  // With screen sections below, only the current archive stays open by default.
  const isOpen = (archiveId: number) =>
    expanded[archiveId] ?? (!children || activeArchiveId == null || activeArchiveId === archiveId);

  return (
    <nav className="rail" aria-label="Library">
      <div className="rail-brand">
        <Link to="/">Study</Link>
        <span className="kicker">local</span>
      </div>

      <div className="rail-body">
        {library.archivesLoaded && library.archives.length === 0 && editing?.kind !== "new-archive" && (
          <p className="rail-empty">No archives yet.</p>
        )}

        {library.archives.map((a) => {
          const open = isOpen(a.id);
          return (
            <div key={a.id} className="rail-group">
              {editing?.kind === "rename-archive" && editing.id === a.id ? (
                <InlineName
                  initial={a.name}
                  placeholder="Archive name"
                  onSubmit={(name) => run(() => api.updateArchive(a.id, { name }))}
                  onCancel={() => setEditing(null)}
                />
              ) : (
                <div className="rail-group-head">
                  <button
                    type="button"
                    className={`kicker ${activeArchiveId === a.id && archiveMatch ? "is-active" : ""}`}
                    title={open ? "Collapse" : "Expand"}
                    onClick={() => setExpanded((s) => ({ ...s, [a.id]: !open }))}
                  >
                    {a.name}
                    {!open && <span className="rail-count"> · {a.notebooks.length}</span>}
                  </button>
                  <span className="rail-actions">
                    <Link className="rail-action" to={`/archive/${a.id}`} title="Open archive — map and chat across notebooks">
                      ↗
                    </Link>
                    <button
                      type="button"
                      className="rail-action"
                      title="New notebook"
                      onClick={() => {
                        setExpanded((s) => ({ ...s, [a.id]: true }));
                        setEditing({ kind: "new-notebook", archiveId: a.id });
                      }}
                    >
                      +
                    </button>
                    <button
                      type="button"
                      className="rail-action"
                      title="Rename archive"
                      onClick={() => setEditing({ kind: "rename-archive", id: a.id, name: a.name })}
                    >
                      ✎
                    </button>
                    <button
                      type="button"
                      className="rail-action"
                      title="Delete archive"
                      onClick={() => handleDeleteArchive(a.id, a.name)}
                    >
                      ×
                    </button>
                  </span>
                </div>
              )}

              {open &&
                a.notebooks.map((nb) =>
                  editing?.kind === "rename-notebook" && editing.id === nb.id ? (
                    <InlineName
                      key={nb.id}
                      initial={nb.name}
                      placeholder="Notebook name"
                      onSubmit={(name) => run(() => api.updateNotebook(nb.id, { name }))}
                      onCancel={() => setEditing(null)}
                    />
                  ) : (
                    <Link
                      key={nb.id}
                      to={`/notebook/${nb.id}`}
                      className={`rail-item ${activeNotebookId === nb.id ? "is-active" : ""}`}
                    >
                      <span className="rail-item-label">{nb.name}</span>
                      <span className="rail-actions">
                        <button
                          type="button"
                          className="rail-action"
                          title="Rename notebook"
                          onClick={(e) => {
                            e.preventDefault();
                            setEditing({ kind: "rename-notebook", id: nb.id, name: nb.name });
                          }}
                        >
                          ✎
                        </button>
                        <button
                          type="button"
                          className="rail-action"
                          title="Delete notebook"
                          onClick={(e) => {
                            e.preventDefault();
                            handleDeleteNotebook(nb.id, nb.name);
                          }}
                        >
                          ×
                        </button>
                      </span>
                      <span className="rail-count">{nb.document_count}</span>
                    </Link>
                  ),
                )}

              {open && editing?.kind === "new-notebook" && editing.archiveId === a.id && (
                <InlineName
                  placeholder="Notebook name"
                  onSubmit={(name) =>
                    run(async () => {
                      const nb = await api.createNotebook(a.id, { name });
                      navigate(`/notebook/${nb.id}`);
                    })
                  }
                  onCancel={() => setEditing(null)}
                />
              )}
              {open && a.notebooks.length === 0 && editing?.kind !== "new-notebook" && (
                <button
                  type="button"
                  className="rail-add"
                  onClick={() => setEditing({ kind: "new-notebook", archiveId: a.id })}
                >
                  + New notebook
                </button>
              )}
            </div>
          );
        })}

        {editing?.kind === "new-archive" ? (
          <InlineName
            placeholder="Archive name"
            onSubmit={(name) => run(() => api.createArchive({ name }))}
            onCancel={() => setEditing(null)}
          />
        ) : (
          !children && (
            <button type="button" className="rail-add" onClick={() => setEditing({ kind: "new-archive" })}>
              + New archive
            </button>
          )
        )}

        {error && <p className="rail-empty error-text">{error}</p>}

        {children}
      </div>

      <div className="rail-foot">
        <span className={`dot ${status.ok ? "dot-cyan" : "dot-magenta"}`} />
        <span title={library.chatStatus?.error ?? undefined}>{status.text}</span>
        <button type="button" className="link" onClick={() => library.setSettingsOpen(true)}>
          Settings
        </button>
      </div>
    </nav>
  );
}
