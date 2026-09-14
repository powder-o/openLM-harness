import { useState } from "react";
import * as api from "../../api";
import { ApiError } from "../../api";
import type { Quiz as QuizType, QuizSubmitResponse, StudyScope } from "../../api";
import { openChunkById } from "../../lib/reader";
import { useStore } from "../../store";
import ScopePicker from "./ScopePicker";

interface Props {
  notebookId: number;
}

export default function Quiz({ notebookId }: Props) {
  const store = useStore();
  const [scope, setScope] = useState<StudyScope>({ kind: "notebook", id: notebookId });
  const [count, setCount] = useState(5);
  const [quiz, setQuiz] = useState<QuizType | null>(null);
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const [result, setResult] = useState<QuizSubmitResponse | null>(null);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function generate() {
    setGenerating(true);
    setError(null);
    setResult(null);
    setAnswers({});
    try {
      setQuiz(await api.generateQuiz({ notebook_id: notebookId, scope, count }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setError("Setting a quiz needs a DeepSeek API key — add one in Settings.");
      else setError(String(e));
    } finally {
      setGenerating(false);
    }
  }

  async function submit() {
    if (!quiz) return;
    try {
      setResult(await api.submitQuiz(quiz.id, quiz.questions.map((q) => answers[q.id] ?? -1)));
      store.refreshDue();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <>
      {!quiz && (
        <section className="side-section">
          <div className="kicker">Set a quiz</div>
          <p className="hint">Multiple-choice questions written from your sources; every explanation links back to its passage.</p>
        </section>
      )}

      {quiz && (
        <section className="side-section">
          {quiz.questions.map((q, qi) => {
            const res = result?.results.find((r) => r.question_id === q.id);
            return (
              <div key={q.id} className="quiz-q">
                <div className="quiz-q-head">
                  <span className="quiz-num">{qi + 1}</span>
                  <p className="quiz-question">{q.question}</p>
                </div>
                <div className="quiz-options">
                  {q.options.map((opt, oi) => {
                    const mark = res
                      ? oi === res.answer_index
                        ? "is-correct"
                        : answers[q.id] === oi
                          ? "is-wrong"
                          : ""
                      : "";
                    return (
                      <label key={oi} className={`check quiz-option ${mark}`}>
                        <input
                          type="radio"
                          name={`q-${q.id}`}
                          checked={answers[q.id] === oi}
                          disabled={!!result}
                          onChange={() => setAnswers((a) => ({ ...a, [q.id]: oi }))}
                        />
                        {opt}
                      </label>
                    );
                  })}
                </div>
                {res && (
                  <p className="quiz-result">
                    <span className={res.correct ? "quiz-ok" : "quiz-bad"}>{res.correct ? "Correct." : "Not quite."}</span>{" "}
                    <span className="muted">{res.explanation}</span>{" "}
                    {res.chunk_id != null && (
                      <button
                        type="button"
                        className="link"
                        onClick={() => openChunkById(res.chunk_id as number, store.openReader)}
                      >
                        Source
                      </button>
                    )}
                  </p>
                )}
              </div>
            );
          })}
          {!result ? (
            <button
              type="button"
              className="btn btn-primary btn-sm side-button"
              onClick={submit}
              disabled={Object.keys(answers).length === 0}
            >
              Check answers
            </button>
          ) : (
            <div className="quiz-score">
              <span className="quiz-score-num">{Math.round(result.score * 100)}%</span>
              <span className="muted">
                {result.results.filter((r) => r.correct).length} of {result.results.length} right
              </span>
            </div>
          )}
        </section>
      )}

      <section className="side-section">
        {quiz && <div className="kicker">Another quiz</div>}
        <div className="study-form">
          <ScopePicker notebookId={notebookId} value={scope} onChange={setScope} />
          <div className="study-form-row">
            <input
              className="input input-sm count-input"
              type="number"
              min={1}
              max={20}
              aria-label="How many questions"
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
            />
            <button type="button" className="btn btn-primary btn-sm" onClick={generate} disabled={generating}>
              {generating ? "Writing questions…" : `Set ${count} questions`}
            </button>
          </div>
        </div>
        {error && <p className="error-text">{error}</p>}
      </section>
    </>
  );
}
