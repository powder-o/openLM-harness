import { useEffect, useState } from "react";
import Modal from "./Modal";
import { getHealth, getSettings, updateSettings, type HealthStatus, type Settings } from "../api";
import { useLibrary } from "../library";

interface Props {
  onClose: () => void;
}

/** API key and models — and, stated plainly, what works without a key. */
export default function SettingsDialog({ onClose }: Props) {
  const library = useLibrary();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [chatModel, setChatModel] = useState("");
  const [extractModel, setExtractModel] = useState("");
  const [visionModel, setVisionModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => {
        setSettings(s);
        setBaseUrl(s.deepseek_base_url);
        setChatModel(s.chat_model);
        setExtractModel(s.extract_model);
        setVisionModel(s.vision_model);
      })
      .catch((e) => setError(String(e)));
    getHealth()
      .then(setHealth)
      .catch(() => {});
  }, []);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const patch: Record<string, string> = {
        deepseek_base_url: baseUrl,
        chat_model: chatModel,
        extract_model: extractModel,
        vision_model: visionModel,
      };
      if (apiKey.trim()) patch.deepseek_api_key = apiKey.trim();
      await updateSettings(patch);
      onClose();
    } catch (e) {
      setError(String(e));
      setSaving(false);
    }
  }

  const harnessFound = library.chatStatus?.runtime_found ?? false;

  return (
    <Modal title="Settings" onClose={onClose} width={520}>
      {!settings ? (
        error ? <p className="error-text">{error}</p> : <p className="hint">Loading…</p>
      ) : (
        <>
          {settings.deepseek_api_key_set ? (
            <div className="notice notice-ok">
              <span className="dot dot-cyan" />
              <p style={{ margin: 0 }}>
                A key is set. Chat, concept extraction, figure descriptions and generated study material are all
                available. Paste a new key below to replace it.
              </p>
            </div>
          ) : (
            <div className="notice">
              <span className="dot dot-magenta" />
              <p style={{ margin: 0 }}>
                No key set. Uploading, search, the structure map and the reader all work without one — chat, concept
                extraction, figure descriptions and generated study material don't.
              </p>
            </div>
          )}

          <div className="field">
            <label htmlFor="settings-key">DeepSeek API key</label>
            <input
              id="settings-key"
              className="input"
              type="password"
              placeholder={
                settings.deepseek_api_key_set ? "sk-…  leave blank to keep the current one" : "sk-…"
              }
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="field">
            <label htmlFor="settings-base">Base URL</label>
            <input id="settings-base" className="input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </div>
          <div className="field-grid">
            <div className="field">
              <label htmlFor="settings-chat">Chat model</label>
              <input
                id="settings-chat"
                className="input"
                value={chatModel}
                onChange={(e) => setChatModel(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="settings-extract">Extract model</label>
              <input
                id="settings-extract"
                className="input"
                value={extractModel}
                onChange={(e) => setExtractModel(e.target.value)}
              />
            </div>
          </div>
          <div className="field">
            <label htmlFor="settings-vision">Vision model — figure descriptions</label>
            <input
              id="settings-vision"
              className="input"
              value={visionModel}
              onChange={(e) => setVisionModel(e.target.value)}
            />
          </div>

          <div className="status-line">
            <span className={`dot ${harnessFound ? "dot-cyan" : "dot-magenta"}`} />
            <span>
              {harnessFound ? "Chat runtime found" : "Chat runtime not found — run uv sync in backend/"}
              {health && ` · embeddings ${health.embed_model.replace(/^.*\//, "")}, local`}
              {health?.fake_llm && " · fake LLM"}
              {health?.fake_embed && " · fake embeddings"}
            </span>
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
