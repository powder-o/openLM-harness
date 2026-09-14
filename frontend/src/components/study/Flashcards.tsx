import { useCallback, useEffect, useState } from "react";
import * as api from "../../api";
import { ApiError } from "../../api";
import type { Flashcard, StudyScope } from "../../api";
import { openChunkById } from "../../lib/reader";
import { useStore } from "../../store";
import ScopePicker from "./ScopePicker";

interface Props {
  notebookId: number;
}

export default function Flashcards({ notebookId }: Props) {
  const store = useStore();
  const [scope, setScope] = useState<StudyScope>({ kind: "notebook", id: notebookId });
  const [count, setCount] = useState(10);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dueCards, setDueCards] = useState<Flashcard[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const { refreshDue } = store;

  const loadDue = useCallback(() => {
    api.listFlashcards(notebookId, true).then((cards) => {
      setDueCards(cards);
      setLoaded(true);
      setIndex(0);
      setRevealed(false);
    });
  }, [notebookId]);

  useEffect(() => {
    loadDue();
  }, [loadDue]);

  async function generate() {
    setGenerating(true);
    setError(null);
    try {
      await api.generateFlashcards({ notebook_id: notebookId, scope, count });
      loadDue();
      refreshDue();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setError("Making cards needs a DeepSeek API key — add one in Settings.");
      else setError(String(e));
    } finally {
      setGenerating(false);
    }
  }

  async function grade(g: 0 | 1 | 2) {
    const card = dueCards[index];
    if (!card) return;
    await api.reviewFlashcard(card.id, g);
    setRevealed(false);
    refreshDue();
    if (index + 1 < dueCards.length) setIndex(index + 1);
    else loadDue();
  }

  const current = dueCards[index];

  return (
    <>
      <section className="side-section">
        <div className="kicker">Review{loaded ? ` · ${dueCards.length} due` : ""}</div>
        {loaded && !current && (
          <p className="hint">Nothing due right now. Cards come back as their intervals run out — or make new ones below.</p>
        )}
        {current && (
          <div className="card flashcard">
            <div className="label-caps">
              Card {index + 1} of {dueCards.length}
            </div>
            <p className="flashcard-front">{current.front}</p>
            {revealed && <p className="flashcard-back">{current.back}</p>}
            <div className="flashcard-actions">
              {!revealed ? (
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setRevealed(true)}>
                  Reveal
                </button>
              ) : (
                <>
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => grade(0)}>
                    Again
                  </button>
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => grade(1)}>
                    Good
                  </button>
                  <button type="button" className="btn btn-primary btn-sm" onClick={() => grade(2)}>
                    Easy
                  </button>
                </>
              )}
              {current.chunk_id != null && (
                <button
                  type="button"
                  className="link link-quiet flashcard-source"
                  onClick={() => openChunkById(current.chunk_id as number, store.openReader)}
                >
                  Source
                </button>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="side-section">
        <div className="kicker">Make cards</div>
        <div className="study-form">
          <ScopePicker notebookId={notebookId} value={scope} onChange={setScope} />
          <div className="study-form-row">
            <input
              className="input input-sm count-input"
              type="number"
              min={1}
              max={50}
              aria-label="How many cards"
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
            />
            <button type="button" className="btn btn-primary btn-sm" onClick={generate} disabled={generating}>
              {generating ? "Making cards…" : `Make ${count} cards`}
            </button>
          </div>
        </div>
        {error && <p className="error-text">{error}</p>}
      </section>
    </>
  );
}
