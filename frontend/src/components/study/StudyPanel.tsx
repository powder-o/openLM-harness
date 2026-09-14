import { useEffect, useState } from "react";
import { useStore } from "../../store";
import type { StudyView } from "../../store";
import Flashcards from "./Flashcards";
import Guides from "./Guides";
import PathView from "./PathView";
import Quiz from "./Quiz";

interface Props {
  notebookId: number;
}

const VIEWS: { key: StudyView; label: string }[] = [
  { key: "flashcards", label: "Cards" },
  { key: "quiz", label: "Quiz" },
  { key: "guides", label: "Guides" },
  { key: "path", label: "Path" },
];

/** The Study tab: spaced-repetition cards, quizzes, guides and learning paths (SPEC §9). */
export default function StudyPanel({ notebookId }: Props) {
  const store = useStore();
  const [view, setView] = useState<StudyView>(store.studyTarget?.view ?? "flashcards");
  const [conceptId, setConceptId] = useState<number | undefined>(store.studyTarget?.conceptId);

  useEffect(() => {
    if (store.studyTarget) {
      setView(store.studyTarget.view);
      if (store.studyTarget.conceptId != null) setConceptId(store.studyTarget.conceptId);
    }
  }, [store.studyTarget]);

  return (
    <div className="side-panel">
      <nav className="subtabs" aria-label="Study">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            type="button"
            className={`subtab ${view === v.key ? "is-on" : ""}`}
            onClick={() => {
              setView(v.key);
              store.setStudyView(v.key);
            }}
          >
            {v.label}
            {v.key === "flashcards" && store.dueCount > 0 && <span className="subtab-count">{store.dueCount}</span>}
          </button>
        ))}
      </nav>
      {view === "flashcards" && <Flashcards notebookId={notebookId} />}
      {view === "quiz" && <Quiz notebookId={notebookId} />}
      {view === "guides" && <Guides notebookId={notebookId} />}
      {view === "path" &&
        (conceptId != null ? (
          <PathView notebookId={notebookId} conceptId={conceptId} />
        ) : (
          <section className="side-section">
            <div className="kicker">Learning path</div>
            <p className="hint">
              Pick a concept on the map, then choose Learning path — the prerequisites your sources support are laid
              out in order, with your mastery of each.
            </p>
            <button type="button" className="btn btn-secondary btn-sm side-button" onClick={store.openMap}>
              Open map
            </button>
          </section>
        ))}
    </div>
  );
}
