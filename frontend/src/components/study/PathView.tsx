import { useEffect, useState } from "react";
import * as api from "../../api";
import { ApiError } from "../../api";
import type { StudyPath } from "../../api";
import { count, countWord } from "../../lib/format";
import { useStore } from "../../store";
import Wedge from "../Wedge";

interface Props {
  notebookId: number;
  conceptId: number;
}

const WEAK = 0.5;

/** Ordered prerequisite path to a concept — a run of steps with mastery on the
 * press wedge; the target carries the actions (SPEC §7 Study tab, §9 path). */
export default function PathView({ notebookId, conceptId }: Props) {
  const store = useStore();
  const [path, setPath] = useState<StudyPath | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"cards" | "guide" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setPath(null);
    setError(null);
    api
      .getStudyPath(conceptId)
      .then(setPath)
      .catch((e) => setError(String(e)));
  }, [conceptId]);

  async function make(kind: "cards" | "guide") {
    if (!path) return;
    setBusy(kind);
    setNotice(null);
    const scope = { kind: "concept" as const, id: path.target.id };
    try {
      if (kind === "cards") {
        await api.generateFlashcards({ notebook_id: notebookId, scope, count: 10 });
        store.refreshDue();
        store.setStudyView("flashcards");
      } else {
        await api.generateGuide({ notebook_id: notebookId, scope });
        store.setStudyView("guides");
      }
    } catch (e) {
      setNotice(e instanceof ApiError && e.status === 409 ? "This needs a DeepSeek API key — add one in Settings." : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (error) return <p className="side-section error-text">{error}</p>;
  if (!path) return <p className="side-section hint">Tracing the path…</p>;

  const weak = path.steps.filter((s, i) => i < path.steps.length - 1 && s.mastery != null && s.mastery < WEAK).length;

  return (
    <section className="side-section">
      <div className="kicker">Path · {count(path.steps.length, "step")} · from the concept map</div>
      <h3 className="path-title">
        Getting to <span className="ink-cyan">{path.target.name}</span>
      </h3>
      <p className="path-dek">
        Ordered by the prerequisite links your sources support.
        {weak > 0 && ` ${countWord(weak, "step")} ${weak === 1 ? "is" : "are"} weak enough to be worth a pass before the target.`}
      </p>

      <ol className="path-steps">
        {path.steps.map((step, i) => {
          const isTarget = i === path.steps.length - 1;
          const pct = step.mastery != null ? Math.round(step.mastery * 100) : null;
          const isWeak = !isTarget && step.mastery != null && step.mastery < WEAK;
          return (
            <li key={step.concept_id} className={`path-step ${isTarget ? "is-target" : ""}`}>
              <span className="path-num">{i + 1}</span>
              <div className="path-body">
                <div className="path-head">
                  <button type="button" className="path-name" onClick={() => store.focusGraphNode(step.node_id)}>
                    {step.name}
                    {isTarget && <span className="path-target-tag">target</span>}
                  </button>
                  <span className={`path-mastery ${isWeak ? "is-weak" : ""}`}>
                    {pct == null ? "not studied" : isWeak ? `weak · ${pct}%` : `mastery ${pct}%`}
                  </span>
                </div>
                {step.summary && <p className="path-summary">{step.summary}</p>}
                <Wedge value={step.mastery} wide />
                {isTarget && (
                  <div className="path-actions">
                    <button type="button" className="btn btn-primary btn-sm" onClick={() => make("cards")} disabled={busy != null}>
                      {busy === "cards" ? "Making…" : "Make 10 cards"}
                    </button>
                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => make("guide")} disabled={busy != null}>
                      {busy === "guide" ? "Writing…" : "Write a guide"}
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => store.focusGraphNode(step.node_id)}>
                      Show in map
                    </button>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {notice && <p className="error-text">{notice}</p>}
    </section>
  );
}
