import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import EvidenceRail from "../components/EvidenceRail";
import NotebookSettingsDialog from "../components/NotebookSettingsDialog";
import Rail from "../components/Rail";
import SourcesPanel from "../components/SourcesPanel";
import StudyPanel from "../components/study/StudyPanel";
import Workspace, { NotebookSection, ThreadsSection } from "../components/Workspace";
import { count, relativeDay } from "../lib/format";
import { findNotebook, useLibrary } from "../library";
import { StoreProvider, useStore } from "../store";
import type { RailTab } from "../store";

/** /notebook/:id — the conversation, with Evidence | Sources | Study beside
 * it and the Reader/Map panes a citation or node click away. */
export default function NotebookPage() {
  const { id } = useParams();
  const location = useLocation();
  const notebookId = Number(id);
  const initialRailTab = (location.state as { railTab?: RailTab } | null)?.railTab;

  if (!notebookId) return <NotFound what="Notebook" />;

  return (
    <StoreProvider key={notebookId} scopeKind="notebook" scopeId={notebookId} initialRailTab={initialRailTab}>
      <NotebookWorkspace notebookId={notebookId} />
    </StoreProvider>
  );
}

function NotebookWorkspace({ notebookId }: { notebookId: number }) {
  const store = useStore();
  const library = useLibrary();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const entry = findNotebook(library.archives, notebookId);

  // Keep the rail's source counts in step with uploads/deletes made here.
  useEffect(() => {
    if (store.documentsLoaded && entry && entry.notebook.document_count !== store.documents.length) {
      library.refreshArchives();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.documentsLoaded, store.documents.length]);

  if (library.archivesLoaded && !entry) return <NotFound what="Notebook" />;

  const concepts = store.graph?.stats.concepts;
  const latestReady = store.documents
    .filter((d) => d.status === "ready")
    .map((d) => d.updated_at)
    .sort()
    .pop();
  const indexing = store.documents.filter((d) => d.status !== "ready" && d.status !== "error").length;
  const meta = (
    <>
      {[
        entry?.archive.name,
        store.documentsLoaded ? count(store.documents.length, "source") : null,
        concepts ? count(concepts, "concept") : null,
        latestReady && !indexing ? `indexed ${relativeDay(latestReady)}` : null,
      ]
        .filter(Boolean)
        .join(" · ")}
      {indexing > 0 && (
        <span className="meta-live">
          <span className="dot dot-yellow dot-pulse" /> indexing {count(indexing, "source")}
        </span>
      )}
    </>
  );

  return (
    <>
      <Workspace
        title={entry?.notebook.name ?? ""}
        meta={meta}
        railSections={
          <>
            <ThreadsSection />
            <NotebookSection onOpenSettings={() => setSettingsOpen(true)} />
          </>
        }
        panels={{
          evidence: <EvidenceRail />,
          sources: <SourcesPanel notebookId={notebookId} />,
          study: <StudyPanel notebookId={notebookId} />,
        }}
      />
      {settingsOpen && <NotebookSettingsDialog notebookId={notebookId} onClose={() => setSettingsOpen(false)} />}
    </>
  );
}

export function NotFound({ what }: { what: string }) {
  return (
    <div className="shell">
      <Rail />
      <main className="front">
        <div className="front-inner">
          <h1 className="front-headline">{what} not found.</h1>
          <p className="front-dek">
            It may have been deleted. <Link to="/">Back to the front page</Link>
          </p>
        </div>
      </main>
    </div>
  );
}
