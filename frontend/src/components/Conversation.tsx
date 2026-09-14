import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import * as api from "../api";
import type { ChatMessage, ToolCallRecord } from "../api";
import { threadTitle } from "../lib/format";
import { parseJson, postSse } from "../lib/sse";
import { useLibrary } from "../library";
import { useStore } from "../store";
import Message, { StreamingAnswer } from "./Message";

const STARTERS = [
  "Summarise the main ideas, with sources",
  "What should I learn first, and why?",
  "Which idea here is hardest? Explain it plainly",
];

interface Props {
  title: string;
  meta: ReactNode;
}

interface StreamingTurn {
  content: string;
  tools: ToolCallRecord[];
}

/** The thread as the page: questions set as pull quotes, answers as body
 * copy with page citations, the composer at the foot (SPEC §11/§12). */
export default function Conversation({ title, meta }: Props) {
  const store = useStore();
  const library = useLibrary();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [allowGeneral, setAllowGeneral] = useState(false);
  const [sending, setSending] = useState(false);
  const [streaming, setStreaming] = useState<StreamingTurn | null>(null);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const creatingRef = useRef(false);
  const stoppedRef = useRef(false);
  const lastTypedRef = useRef("");
  const { threadId, setLastAnswer } = store;
  const narrow = store.pane != null;
  const chatStatus = library.chatStatus;

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    // The thread we just created for an in-flight question: keep its turn.
    if (creatingRef.current) {
      creatingRef.current = false;
      return;
    }
    abortRef.current?.abort();
    setError(null);
    if (!threadId) {
      setMessages([]);
      setLoaded(true);
      setLastAnswer(null);
      return;
    }
    let cancelled = false;
    setLoaded(false);
    api
      .getChatMessages(threadId)
      .then((msgs) => {
        if (cancelled) return;
        setMessages(msgs);
        setLoaded(true);
        setLastAnswer([...msgs].reverse().find((m) => m.role === "assistant") ?? null);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e));
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [threadId, setLastAnswer]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight });
  }, [messages, streaming, narrow]);

  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [store.composerDraft, narrow]);

  // Text quoted in from the Reader ("Ask about this") lands focused.
  useEffect(() => {
    const el = inputRef.current;
    if (!el || store.composerDraft === lastTypedRef.current) return;
    lastTypedRef.current = store.composerDraft;
    if (store.composerDraft && document.activeElement !== el) {
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  }, [store.composerDraft]);

  async function send() {
    const content = store.composerDraft.trim();
    if (!content || sending) return;
    setSending(true);
    setError(null);

    let sid = threadId;
    if (!sid) {
      try {
        creatingRef.current = true;
        sid = await store.ensureThread();
      } catch (e) {
        creatingRef.current = false;
        setError(String(e));
        setSending(false);
        return;
      }
    }

    const optimistic: ChatMessage = {
      id: `pending-${Date.now()}`,
      role: "user",
      content,
      citations: [],
      highlight: null,
      tool_calls: [],
      created_at: new Date().toISOString(),
    };
    setMessages((m) => [...m, optimistic]);
    lastTypedRef.current = "";
    store.setComposerDraft("");
    setStreaming({ content: "", tools: [] });

    const controller = new AbortController();
    abortRef.current = controller;
    stoppedRef.current = false;
    try {
      await postSse(
        api.chatMessagesUrl(sid),
        { content, allow_general_knowledge: allowGeneral },
        (evt) => {
          if (evt.event === "tool_call") {
            const tc = parseJson<{ id: string; name: string; arguments: Record<string, unknown> }>(evt.data);
            setStreaming((s) => (s ? { ...s, tools: [...s.tools, tc] } : s));
          } else if (evt.event === "tool_result") {
            const tr = parseJson<{ id: string; name: string; summary: string }>(evt.data);
            setStreaming((s) =>
              s ? { ...s, tools: s.tools.map((t) => (t.id === tr.id ? { ...t, summary: tr.summary } : t)) } : s,
            );
          } else if (evt.event === "delta") {
            const d = parseJson<{ text: string }>(evt.data);
            setStreaming((s) => (s ? { ...s, content: s.content + d.text } : s));
          } else if (evt.event === "done") {
            const d = parseJson<{ message: ChatMessage }>(evt.data);
            setMessages((m) => [...m, d.message]);
            setStreaming(null);
            setLastAnswer(d.message, true);
            store.refreshThreads();
          } else if (evt.event === "error") {
            const d = parseJson<{ message: string }>(evt.data);
            setError(d.message);
            setStreaming(null);
          }
        },
        controller.signal,
      );
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
      setStreaming(null);
      if (abortRef.current === controller) abortRef.current = null;
      if (stoppedRef.current) {
        api
          .getChatMessages(sid)
          .then(setMessages)
          .catch(() => {});
      }
    }
  }

  function stop() {
    stoppedRef.current = true;
    abortRef.current?.abort();
  }

  const currentThread = store.threads.find((t) => t.id === threadId);
  const placeholder = narrow
    ? store.pane === "reader"
      ? "Ask about this page…"
      : "Ask about the map…"
    : `Ask about ${title || "these sources"}…`;

  return (
    <div className="conversation">
      <header className="conv-head">
        <div className="conv-title-block">
          <h3 className="conv-title">{title}</h3>
          <div className="conv-meta">{narrow ? (currentThread ? threadTitle(currentThread) : "New thread") : meta}</div>
        </div>
        {!narrow && (
          <div className="conv-head-actions">
            {store.threads.length > 0 && (
              <select
                className="input input-sm thread-picker"
                aria-label="Thread"
                value={threadId ?? ""}
                onChange={(e) => (e.target.value ? store.setThreadId(e.target.value) : store.newThread())}
              >
                {threadId == null && <option value="">New thread</option>}
                {store.threads.map((t) => (
                  <option key={t.id} value={t.id}>
                    {threadTitle(t)}
                  </option>
                ))}
              </select>
            )}
            <button type="button" className="btn btn-ghost btn-sm" onClick={store.newThread}>
              New thread
            </button>
          </div>
        )}
      </header>

      <div className="conv-scroll" ref={listRef}>
        <div className="conv-turns">
          {loaded && messages.length === 0 && !streaming && (
            <div className="conv-empty">
              <div className="kicker kicker-cyan">A new thread</div>
              <p className="question">What do you want to understand{title ? ` in ${title}` : ""}?</p>
              <p className="conv-empty-dek">
                Answers come only from{" "}
                {store.scopeKind === "archive" ? "the notebooks in this archive" : "this notebook's sources"}, and
                every claim is pinned to a passage you can open.
              </p>
              {store.scopeKind === "notebook" && store.documentsLoaded && store.documents.length === 0 ? (
                <p className="conv-empty-dek">
                  There are no sources here yet.{" "}
                  <button type="button" className="link" onClick={() => store.setRailTab("sources")}>
                    Add some in Sources
                  </button>{" "}
                  to begin.
                </p>
              ) : (
                <div className="prompt-chips">
                  {STARTERS.map((s) => (
                    <button key={s} type="button" className="prompt-chip" onClick={() => store.setComposerDraft(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {messages.map((m) => (
            <Message key={m.id} message={m} />
          ))}
          {streaming && <StreamingAnswer content={streaming.content} calls={streaming.tools} />}
          {error && (
            <div className="turn-error">
              <span className="dot dot-magenta" />
              <span>{error}</span>
              {chatStatus && !chatStatus.ready && (
                <button type="button" className="link" onClick={() => library.setSettingsOpen(true)}>
                  Open Settings
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="composer-wrap">
        {chatStatus && !chatStatus.ready && (
          <div className="composer-warning">
            <span className="dot dot-magenta" />
            <span>
              {!chatStatus.api_key_set
                ? "Asking needs a DeepSeek API key."
                : !chatStatus.runtime_found
                  ? "The chat runtime isn't installed — run uv sync in backend/."
                  : `The chat agent reported an error: ${chatStatus.error ?? "unknown"}`}
            </span>
            <button type="button" className="link" onClick={() => library.setSettingsOpen(true)}>
              Settings
            </button>
          </div>
        )}
        <div className={`composer ${narrow ? "is-compact" : ""}`}>
          <textarea
            ref={inputRef}
            className="composer-input"
            rows={1}
            value={store.composerDraft}
            placeholder={placeholder}
            onChange={(e) => {
              lastTypedRef.current = e.target.value;
              store.setComposerDraft(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
          />
          <div className="composer-row">
            {!narrow && (
              <div className="seg" role="radiogroup" aria-label="Grounding">
                <button
                  type="button"
                  role="radio"
                  aria-checked={!allowGeneral}
                  className={`seg-opt ${!allowGeneral ? "is-on" : ""}`}
                  onClick={() => setAllowGeneral(false)}
                >
                  Sources only
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={allowGeneral}
                  className={`seg-opt ${allowGeneral ? "is-on" : ""}`}
                  onClick={() => setAllowGeneral(true)}
                >
                  + General knowledge
                </button>
              </div>
            )}
            {sending ? (
              <button type="button" className="btn btn-secondary btn-sm" onClick={stop}>
                Stop
              </button>
            ) : (
              <button
                type="button"
                className={`btn btn-primary ${narrow ? "btn-sm" : ""}`}
                onClick={send}
                disabled={!store.composerDraft.trim()}
              >
                Ask
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
