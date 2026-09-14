import { useEffect, useState } from "react";
import * as api from "../api";
import type { NotebookSettings } from "../api";
import { useLibrary } from "../library";
import Modal from "./Modal";

interface Props {
  notebookId: number;
  onClose: () => void;
}

const OCR_MODES: NotebookSettings["ocr"][] = ["auto", "force", "off"];

/** A notebook's name and ingestion settings (SPEC §5) — formerly the foot of the Sources tab. */
export default function NotebookSettingsDialog({ notebookId, onClose }: Props) {
  const library = useLibrary();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [form, setForm] = useState<NotebookSettings | null>(null);
  const [ocrLangText, setOcrLangText] = useState("en");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getNotebook(notebookId)
      .then((nb) => {
        setName(nb.name);
        setDescription(nb.description ?? "");
        setForm(nb.settings);
        setOcrLangText(nb.settings.ocr_lang.join(", "));
      })
      .catch((e) => setError(String(e)));
  }, [notebookId]);

  async function save() {
    if (!form) return;
    setSaving(true);
    setError(null);
    try {
      await api.updateNotebook(notebookId, {
        name: name.trim() || undefined,
        description,
        settings: {
          ...form,
          ocr_lang: ocrLangText
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        },
      });
      library.refreshArchives();
      onClose();
    } catch (e) {
      setError(String(e));
      setSaving(false);
    }
  }

  const set = <K extends keyof NotebookSettings>(key: K, value: NotebookSettings[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f));

  return (
    <Modal title="Notebook settings" onClose={onClose} width={540}>
      {!form ? (
        error ? <p className="error-text">{error}</p> : <p className="hint">Loading…</p>
      ) : (
        <>
          <div className="field">
            <label htmlFor="nb-name">Name</label>
            <input id="nb-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="nb-desc">Description</label>
            <input
              id="nb-desc"
              className="input"
              placeholder="What this notebook is for"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="form-section">
            <div className="kicker">Ingestion</div>
            <p className="hint" style={{ margin: 0 }}>
              Changes apply the next time a source is ingested or re-ingested.
            </p>
          </div>

          <div className="field-grid">
            <div className="field">
              <span className="field-label">OCR</span>
              <div className="seg">
                {OCR_MODES.map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    className={`seg-opt ${form.ocr === mode ? "is-on" : ""}`}
                    onClick={() => set("ocr", mode)}
                  >
                    {mode}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label htmlFor="nb-ocr-lang">OCR languages</label>
              <input
                id="nb-ocr-lang"
                className="input"
                placeholder="en, de"
                value={ocrLangText}
                onChange={(e) => setOcrLangText(e.target.value)}
              />
            </div>
          </div>

          <div className="field-grid">
            <div className="field">
              <label htmlFor="nb-chunk">Passage size, tokens</label>
              <input
                id="nb-chunk"
                className="input"
                type="number"
                value={form.chunk_max_tokens}
                onChange={(e) => set("chunk_max_tokens", Number(e.target.value))}
              />
            </div>
            <div className="field">
              <label htmlFor="nb-overlap">Overlap, tokens</label>
              <input
                id="nb-overlap"
                className="input"
                type="number"
                value={form.chunk_overlap_tokens}
                onChange={(e) => set("chunk_overlap_tokens", Number(e.target.value))}
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor="nb-depth">Map section depth</label>
            <input
              id="nb-depth"
              className="input"
              type="number"
              min={1}
              max={6}
              style={{ maxWidth: 120 }}
              value={form.graph_section_depth}
              onChange={(e) => set("graph_section_depth", Number(e.target.value))}
            />
          </div>

          <div className="form-section">
            <label className="check">
              <input
                type="checkbox"
                checked={form.table_structure}
                onChange={(e) => set("table_structure", e.target.checked)}
              />
              Recognise table structure
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={form.describe_figures}
                onChange={(e) => set("describe_figures", e.target.checked)}
              />
              Describe figures with the vision model
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={form.extract_concepts}
                onChange={(e) => set("extract_concepts", e.target.checked)}
              />
              Extract concepts for the map
            </label>
          </div>

          {error && <p className="error-text">{error}</p>}
          <div className="dialog-actions">
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
