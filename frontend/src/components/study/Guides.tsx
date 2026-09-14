import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import * as api from "../../api";
import { ApiError } from "../../api";
import type { Guide, StudyScope } from "../../api";
import { injectCitationLinks, parseCiteAnchor } from "../../lib/citations";
import { relativeDay } from "../../lib/format";
import { openChunkById } from "../../lib/reader";
import { useStore } from "../../store";
import ScopePicker from "./ScopePicker";

interface Props {
  notebookId: number;
}

export default function Guides({ notebookId }: Props) {
  const store = useStore();
  const [scope, setScope] = useState<StudyScope>({ kind: "notebook", id: notebookId });
  const [guides, setGuides] = useState<Guide[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<Guide | null>(null);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listGuides(notebookId)
      .then(setGuides)
      .finally(() => setLoaded(true));
  }, [notebookId]);

  async function generate() {
    setGenerating(true);
    setError(null);
    try {
      const guide = await api.generateGuide({ notebook_id: notebookId, scope });
      setGuides((g) => [guide, ...g]);
      setSelected(guide);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setError("Writing a guide needs a DeepSeek API key — add one in Settings.");
      else setError(String(e));
    } finally {
      setGenerating(false);
    }
  }

  const components: Components = {
    a: ({ href, children }) => {
      const parsed = href ? parseCiteAnchor(href) : null;
      if (!parsed) {
        return (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        );
      }
      return (
        <button type="button" className="cite" onClick={() => openChunkById(parsed.id, store.openReader)}>
          source
        </button>
      );
    },
  };

  if (selected) {
    return (
      <section className="side-section">
        <button type="button" className="link link-quiet side-back" onClick={() => setSelected(null)}>
          ← All guides
        </button>
        <div className="kicker">Guide · {relativeDay(selected.created_at)}</div>
        <h4 className="guide-title">{selected.title}</h4>
        <div className="prose prose-sm">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
            {injectCitationLinks(selected.content_md)}
          </ReactMarkdown>
        </div>
      </section>
    );
  }

  return (
    <>
      <section className="side-section">
        <div className="kicker">Write a guide</div>
        <div className="study-form">
          <ScopePicker notebookId={notebookId} value={scope} onChange={setScope} />
          <div className="study-form-row">
            <button type="button" className="btn btn-primary btn-sm" onClick={generate} disabled={generating}>
              {generating ? "Writing…" : "Write a guide"}
            </button>
          </div>
        </div>
        {error && <p className="error-text">{error}</p>}
      </section>

      <section className="side-section">
        <div className="kicker">Guides · {guides.length}</div>
        {loaded && guides.length === 0 && (
          <p className="hint">No guides yet. A guide walks through a scope in order, citing the passages as it goes.</p>
        )}
        <div className="listings">
          {guides.map((g) => (
            <button key={g.id} type="button" className="listing" onClick={() => setSelected(g)}>
              <span className="listing-title">{g.title}</span>
              <span className="listing-meta">{relativeDay(g.created_at)}</span>
            </button>
          ))}
        </div>
      </section>
    </>
  );
}
