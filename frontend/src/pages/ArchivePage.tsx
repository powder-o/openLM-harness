import { Link, useParams } from "react-router-dom";
import type { Archive } from "../api";
import EvidenceRail from "../components/EvidenceRail";
import Workspace, { ThreadsSection } from "../components/Workspace";
import { count } from "../lib/format";
import { useLibrary } from "../library";
import { StoreProvider, useStore } from "../store";
import { NotFound } from "./NotebookPage";

/** /archive/:id — archive-scoped conversation across its notebooks, with the
 * merged map and the notebook listing beside it (SPEC §12). */
export default function ArchivePage() {
  const { id } = useParams();
  const archiveId = Number(id);

  if (!archiveId) return <NotFound what="Archive" />;

  return (
    <StoreProvider key={archiveId} scopeKind="archive" scopeId={archiveId}>
      <ArchiveWorkspace archiveId={archiveId} />
    </StoreProvider>
  );
}

function ArchiveWorkspace({ archiveId }: { archiveId: number }) {
  const store = useStore();
  const library = useLibrary();
  const archive = library.archives.find((a) => a.id === archiveId);

  if (library.archivesLoaded && !archive) return <NotFound what="Archive" />;

  const sources = archive?.notebooks.reduce((s, n) => s + n.document_count, 0) ?? 0;
  const concepts = store.graph?.stats.concepts;
  const meta = [
    "Archive",
    archive ? count(archive.notebooks.length, "notebook") : null,
    archive ? count(sources, "source") : null,
    concepts ? `${count(concepts, "concept")} across notebooks` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Workspace
      title={archive?.name ?? ""}
      meta={meta}
      railSections={<ThreadsSection />}
      panels={{
        evidence: <EvidenceRail />,
        notebooks: archive ? <ArchiveNotebooks archive={archive} /> : null,
      }}
    />
  );
}

function ArchiveNotebooks({ archive }: { archive: Archive }) {
  return (
    <div className="side-panel">
      <div className="kicker">Notebooks in this archive · {archive.notebooks.length}</div>
      {archive.description && <p className="hint">{archive.description}</p>}
      {archive.notebooks.length === 0 && (
        <p className="hint">No notebooks yet — add one from the rail with the + beside the archive name.</p>
      )}
      <div className="listings">
        {archive.notebooks.map((nb) => (
          <Link key={nb.id} to={`/notebook/${nb.id}`} className="listing">
            <span className="listing-title">{nb.name}</span>
            <span className="listing-meta">
              {count(nb.document_count, "source")}
              {nb.description ? ` · ${nb.description}` : ""}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
