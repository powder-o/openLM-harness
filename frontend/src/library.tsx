// App-level state every screen reads: the archive → notebook tree (the rail,
// the front page and the archive page all render it), the chat agent status
// (the rail footer's dot) and the Settings dialog.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import * as api from "./api";
import type { Archive, ChatStatus, NotebookSummary } from "./api";

interface LibraryState {
  archives: Archive[];
  archivesLoaded: boolean;
  refreshArchives: () => void;
  chatStatus: ChatStatus | null;
  refreshStatus: () => void;
  settingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;
}

const LibraryContext = createContext<LibraryState | null>(null);

export function LibraryProvider({ children }: { children: ReactNode }) {
  const [archives, setArchives] = useState<Archive[]>([]);
  const [archivesLoaded, setArchivesLoaded] = useState(false);
  const [chatStatus, setChatStatus] = useState<ChatStatus | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const refreshArchives = useCallback(() => {
    api
      .listArchives()
      .then(setArchives)
      .catch(() => {})
      .finally(() => setArchivesLoaded(true));
  }, []);

  const refreshStatus = useCallback(() => {
    api
      .getChatStatus()
      .then(setChatStatus)
      .catch(() => setChatStatus(null));
  }, []);

  useEffect(() => {
    refreshArchives();
    refreshStatus();
  }, [refreshArchives, refreshStatus]);

  const value = useMemo(
    () => ({
      archives,
      archivesLoaded,
      refreshArchives,
      chatStatus,
      refreshStatus,
      settingsOpen,
      setSettingsOpen,
    }),
    [archives, archivesLoaded, refreshArchives, chatStatus, refreshStatus, settingsOpen],
  );

  return <LibraryContext.Provider value={value}>{children}</LibraryContext.Provider>;
}

export function useLibrary(): LibraryState {
  const ctx = useContext(LibraryContext);
  if (!ctx) throw new Error("useLibrary must be used within a LibraryProvider");
  return ctx;
}

export interface NotebookEntry {
  notebook: NotebookSummary;
  archive: Archive;
}

export function findNotebook(archives: Archive[], notebookId: number): NotebookEntry | null {
  for (const archive of archives) {
    const notebook = archive.notebooks.find((n) => n.id === notebookId);
    if (notebook) return { notebook, archive };
  }
  return null;
}

/** "Statistical Mechanics" → "SM", "Thermodynamics" → "Th" — the folded rail's marks. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  const word = words[0] ?? "?";
  return word[0].toUpperCase() + (word[1] ?? "").toLowerCase();
}

/** One line for the chat agent's state, used by the rail footer. */
export function statusLine(status: ChatStatus | null): { ok: boolean; text: string } {
  if (!status) return { ok: false, text: "Backend unreachable" };
  if (status.ready) return { ok: true, text: "Chat ready" };
  if (!status.api_key_set) return { ok: false, text: "API key not set" };
  if (!status.runtime_found) return { ok: false, text: "Chat runtime missing" };
  return { ok: false, text: "Chat agent error" };
}
