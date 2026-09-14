import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import * as api from "../api";
import type { ChatSession, DocumentSummary } from "../api";
import AddSourceDialog, { type AddMode } from "../components/AddSourceDialog";
import Rail from "../components/Rail";
import Wedge from "../components/Wedge";
import { count, countWord } from "../lib/format";
import { useLibrary, type NotebookEntry } from "../library";

interface CardDetail {
  docs: DocumentSummary[];
  concepts: number | null;
  mastery: number | null;
}

function cardLine(detail: CardDetail | undefined, thread: ChatSession | undefined): string {
  if (!detail) return "…";
  const { docs } = detail;
  const active = docs.filter((d) => d.status !== "ready" && d.status !== "error");
  if (active.length) {
    const first = active[0];
    return `${countWord(active.length, "source")} still indexing: “${first.title}” is ${
      first.status_detail || first.status
    }.`;
  }
  if (thread?.title && thread.title !== "New chat") return `Last thread: “${thread.title}”`;
  const failed = docs.filter((d) => d.status === "error").length;
  if (failed) return `${countWord(failed, "source")} failed to ingest — open Sources to retry.`;
  if (docs.length === 0) return "No sources yet — add a PDF, a web page or a note.";
  return `${countWord(docs.length, "source")} ready. Ask the first question.`;
}

/** `/` — the workspace as a front page. Notebooks you were last in are the
 * only boxed things on it. */
export default function Home() {
  const library = useLibrary();
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [due, setDue] = useState<Record<number, number>>({});
  const [details, setDetails] = useState<Record<number, CardDetail>>({});
  const [adding, setAdding] = useState<AddMode | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const entries: NotebookEntry[] = useMemo(
    () => library.archives.flatMap((archive) => archive.notebooks.map((notebook) => ({ notebook, archive }))),
    [library.archives],
  );
  const idsKey = entries.map((e) => e.notebook.id).join(",");

  useEffect(() => {
    api
      .listAllChatSessions()
      .then(setSessions)
      .catch(() => {});
  }, []);

  useEffect(() => {
    Promise.all(
      entries.map((e) =>
        api
          .listFlashcards(e.notebook.id, true)
          .then((cards) => [e.notebook.id, cards.length] as const)
          .catch(() => [e.notebook.id, 0] as const),
      ),
    ).then((pairs) => setDue(Object.fromEntries(pairs)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey]);

  // Most recent thread per notebook (sessions arrive newest first).
  const lastThread = useMemo(() => {
    const map = new Map<number, ChatSession>();
    for (const s of sessions) {
      const m = /^notebook:(\d+)$/.exec(s.scope);
      if (m && !map.has(Number(m[1]))) map.set(Number(m[1]), s);
    }
    return map;
  }, [sessions]);

  const continueList = useMemo(
    () =>
      [...entries]
        .sort((a, b) =>
          (lastThread.get(b.notebook.id)?.updated_at ?? "").localeCompare(
            lastThread.get(a.notebook.id)?.updated_at ?? "",
          ),
        )
        .slice(0, 3),
    [entries, lastThread],
  );
  const continueKey = continueList.map((e) => e.notebook.id).join(",");

  useEffect(() => {
    for (const { notebook } of continueList) {
      const id = notebook.id;
      Promise.all([
        api.listDocuments(id).catch(() => [] as DocumentSummary[]),
        api
          .getGraph("notebook", id)
          .then((g) => g.stats.concepts)
          .catch(() => null),
        api
          .getMastery(id)
          .then((m) => {
            const values = Object.values(m);
            return values.length ? values.reduce((s, v) => s + v, 0) / values.length : null;
          })
          .catch(() => null),
      ]).then(([docs, concepts, mastery]) =>
        setDetails((d) => ({ ...d, [id]: { docs, concepts, mastery } })),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [continueKey]);

  const totalSources = entries.reduce((s, e) => s + e.notebook.document_count, 0);
  const totalDue = Object.values(due).reduce((s, n) => s + n, 0);
  const withThreads = entries.filter((e) => lastThread.has(e.notebook.id)).length;
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });

  const dekParts: string[] = [];
  if (withThreads) dekParts.push(`${countWord(withThreads, "notebook")} ${withThreads === 1 ? "has" : "have"} threads to pick up`);
  if (totalDue) dekParts.push(`${count(totalDue, "flashcard")} ${totalDue === 1 ? "is" : "are"} due today`);
  const dek =
    (dekParts.length ? `${dekParts.join(" and ")}. ` : "") +
    "Everything here is grounded in sources you uploaded — nothing else.";

  async function createFirst() {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    setError(null);
    try {
      if (library.archives.length === 0) {
        await api.createArchive({ name });
        setNewName("");
        library.refreshArchives();
      } else {
        const nb = await api.createNotebook(library.archives[0].id, { name });
        library.refreshArchives();
        navigate(`/notebook/${nb.id}`, { state: { railTab: "sources" } });
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  }

  const noArchives = library.archivesLoaded && library.archives.length === 0;
  const noNotebooks = library.archivesLoaded && library.archives.length > 0 && entries.length === 0;

  return (
    <div className="shell">
      <Rail />
      <main className="front">
        <div className="front-inner">
          <div className="masthead-thick" />
          <div className="dateline">
            <span>{today}</span>
            <span>
              {count(library.archives.length, "archive")} · {count(entries.length, "notebook")}
            </span>
            <span>{count(totalSources, "source")} indexed</span>
            <span>Sources only</span>
          </div>
          <div className="masthead-thin" />

          {noArchives || noNotebooks ? (
            <>
              <h1 className="front-headline">{noArchives ? "Start a study archive." : "Now open a notebook."}</h1>
              <p className="front-dek">
                {noArchives
                  ? "An archive holds notebooks; a notebook holds the books, papers and notes you want to study. Every answer points back at the exact spot in them."
                  : `A notebook inside “${library.archives[0].name}” holds the sources you want to study together.`}
              </p>
              <div className="front-empty front-actions-row">
                <input
                  className="input"
                  placeholder={noArchives ? "Archive name, e.g. Physics" : "Notebook name, e.g. Statistical Mechanics"}
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && createFirst()}
                />
                <button type="button" className="btn btn-primary" onClick={createFirst} disabled={creating || !newName.trim()}>
                  {noArchives ? "Create archive" : "Create notebook"}
                </button>
              </div>
              {error && <p className="error-text">{error}</p>}
            </>
          ) : (
            <>
              <h1 className="front-headline">
                {withThreads ? "Pick up where you stopped reading." : "Ask your sources something."}
              </h1>
              <p className="front-dek">{dek}</p>

              {continueList.length > 0 && (
                <section className="front-section">
                  <div className="kicker kicker-lg">{withThreads ? "Continue" : "Notebooks"}</div>
                  <div className="continue-grid">
                    {continueList.map(({ notebook, archive }) => {
                      const detail = details[notebook.id];
                      const cardsDue = due[notebook.id] ?? 0;
                      return (
                        <Link key={notebook.id} to={`/notebook/${notebook.id}`} className="card notebook-card">
                          <div className="label-caps">
                            {archive.name} · {count(notebook.document_count, "source")}
                          </div>
                          <div className="notebook-card-title">{notebook.name}</div>
                          <p className="notebook-card-body">{cardLine(detail, lastThread.get(notebook.id))}</p>
                          <div className="notebook-card-meta">
                            <span>{count(cardsDue, "card")} due</span>
                            {detail?.concepts != null && (
                              <>
                                <span>·</span>
                                <span>{count(detail.concepts, "concept")}</span>
                              </>
                            )}
                          </div>
                          <Wedge value={detail?.mastery ?? null} />
                        </Link>
                      );
                    })}
                  </div>
                </section>
              )}

              <div className="front-foot">
                <div className="front-actions">
                  <div className="kicker kicker-lg">Add to a notebook</div>
                  <div className="front-actions-row">
                    <button type="button" className="btn btn-primary" onClick={() => setAdding("upload")}>
                      Upload sources
                    </button>
                    <button type="button" className="btn btn-secondary" onClick={() => setAdding("url")}>
                      Add a web page
                    </button>
                    <button type="button" className="btn btn-ghost" onClick={() => setAdding("note")}>
                      Write a note
                    </button>
                  </div>
                </div>
                <p className="front-note">
                  PDF, DOCX, HTML, Markdown, plain text or a URL. Parsing keeps page and position, so every answer can
                  point back at the exact spot.
                </p>
              </div>
            </>
          )}
        </div>
      </main>
      {adding && (
        <AddSourceDialog
          mode={adding}
          defaultNotebookId={continueList[0]?.notebook.id}
          onClose={() => setAdding(null)}
        />
      )}
    </div>
  );
}
