import type { ReactNode } from "react";
import { threadTitle } from "../lib/format";
import { useStore } from "../store";
import type { RailTab } from "../store";
import Conversation from "./Conversation";
import GraphView from "./GraphView";
import PaneBoundary from "./PaneBoundary";
import Rail from "./Rail";
import ReaderPanel from "./ReaderPanel";

const TAB_LABELS: Record<RailTab, string> = {
  evidence: "Evidence",
  sources: "Sources",
  study: "Study",
  notebooks: "Notebooks",
};

interface Props {
  title: string;
  meta: ReactNode;
  railSections: ReactNode;
  /** Side-rail tabs for this scope, in display order. */
  panels: Partial<Record<RailTab, ReactNode>>;
}

/** The notebook/archive layout. The answer is the page; the side rail carries
 * evidence and tools. Opening a citation or the map hands the right side to a
 * Reader/Map pane: the left rail folds to initials and the thread narrows. */
export default function Workspace({ title, meta, railSections, panels }: Props) {
  const store = useStore();
  const tabs = Object.keys(panels) as RailTab[];
  const railTab = tabs.includes(store.railTab) ? store.railTab : tabs[0];

  return (
    <div className="shell">
      <Rail folded={store.pane != null}>{railSections}</Rail>
      <main className={`conversation-col ${store.pane ? "is-narrow" : ""}`}>
        <Conversation title={title} meta={meta} />
      </main>
      {store.pane === "reader" && (
        <PaneBoundary label="The reader" asPane onClose={store.closePane}>
          <ReaderPanel />
        </PaneBoundary>
      )}
      {store.pane === "map" && (
        <PaneBoundary label="The map" asPane onClose={store.closePane}>
          <GraphView />
        </PaneBoundary>
      )}
      {!store.pane && (
        <aside className="side-rail" aria-label="Evidence and tools">
          <div className="seg side-rail-tabs" role="tablist">
            {tabs.map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={railTab === key}
                className={`seg-opt ${railTab === key ? "is-on" : ""}`}
                onClick={() => store.setRailTab(key)}
              >
                {TAB_LABELS[key]}
                {key === "evidence" && store.evidenceHasUpdate && railTab !== "evidence" && (
                  <span className="seg-badge" aria-label="new" />
                )}
              </button>
            ))}
          </div>
          <div className="side-rail-body">
            <PaneBoundary key={railTab} label={TAB_LABELS[railTab]}>
              {panels[railTab]}
            </PaneBoundary>
          </div>
        </aside>
      )}
    </div>
  );
}

/** Left-rail section: this scope's threads. */
export function ThreadsSection() {
  const store = useStore();
  return (
    <div className="rail-section">
      <div className="kicker">Threads</div>
      {store.threadId == null && store.threads.length > 0 && (
        <div className="rail-sub is-active">
          <span className="rail-sub-label">New thread</span>
        </div>
      )}
      {store.threads.map((t) => {
        const label = threadTitle(t);
        return (
          <div key={t.id} className={`rail-sub ${t.id === store.threadId ? "is-active" : ""}`}>
            <button type="button" className="rail-sub-label" title={label} onClick={() => store.setThreadId(t.id)}>
              {label}
            </button>
            <span className="rail-actions">
              <button
                type="button"
                className="rail-action"
                title="Delete thread"
                onClick={() => {
                  if (confirm(`Delete the thread “${label}”?`)) store.deleteThread(t.id).catch(console.error);
                }}
              >
                ×
              </button>
            </span>
          </div>
        );
      })}
      <button type="button" className="rail-add" onClick={store.newThread}>
        + New thread
      </button>
    </div>
  );
}

/** Left-rail section: a notebook's sources, study and ingestion settings. */
export function NotebookSection({ onOpenSettings }: { onOpenSettings: () => void }) {
  const store = useStore();
  const on = (tab: RailTab) => (store.railTab === tab && !store.pane ? "is-active" : "");
  return (
    <div className="rail-section">
      <div className="kicker">Notebook</div>
      <button type="button" className={`rail-sub ${on("sources")}`} onClick={() => store.setRailTab("sources")}>
        Sources <span className="rail-count">{store.documentsLoaded ? store.documents.length : ""}</span>
      </button>
      <button type="button" className={`rail-sub ${on("study")}`} onClick={() => store.setRailTab("study")}>
        Study {store.dueCount > 0 && <span className="rail-count">{store.dueCount} due</span>}
      </button>
      <button type="button" className={`rail-sub ${store.pane === "map" ? "is-active" : ""}`} onClick={store.openMap}>
        Map
      </button>
      <button type="button" className="rail-sub" onClick={onOpenSettings}>
        Settings
      </button>
    </div>
  );
}
