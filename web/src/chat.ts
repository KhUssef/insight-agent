// The client-side model of a chat: a titled sequence of runs bound to one
// model and one data folder, persisted in localStorage across reloads.

import { Run, applyStopped } from "./run";

export interface Chat {
  id: string;
  title: string;
  createdAt: number;
  model: string;
  folder: string | null;
  runs: Run[];
}

const CHATS_KEY = "insight-chats";
const FOLDERS_KEY = "insight-folders";

// Stored runs are capped per chat so the persisted payload respects the
// localStorage quota; chart PNGs stay server-side and are referenced by URL.
const MAX_STORED_RUNS = 20;

interface StoredChats {
  chats: Chat[];
  activeId: string | null;
}

function makeId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `chat-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

export function newChat(model: string, folder: string | null): Chat {
  return {
    id: makeId(),
    title: "New chat",
    createdAt: Date.now(),
    model,
    folder,
    runs: [],
  };
}

// A run restored from storage can no longer be streaming; anything that was
// live when the page unloaded comes back as stopped.
function restoreRun(run: Run): Run {
  return run.status === "running" ? applyStopped(run) : run;
}

export function loadChats(): StoredChats {
  try {
    const raw = window.localStorage.getItem(CHATS_KEY);
    if (!raw) {
      return { chats: [], activeId: null };
    }
    const parsed = JSON.parse(raw) as StoredChats;
    if (!Array.isArray(parsed.chats)) {
      return { chats: [], activeId: null };
    }
    const chats = parsed.chats.map((chat) => ({
      ...chat,
      runs: (chat.runs ?? []).map(restoreRun),
    }));
    const activeId = chats.some((chat) => chat.id === parsed.activeId)
      ? parsed.activeId
      : (chats[0]?.id ?? null);
    return { chats, activeId };
  } catch {
    return { chats: [], activeId: null };
  }
}

export function saveChats(chats: Chat[], activeId: string | null): void {
  const trimmed = chats.map((chat) => ({
    ...chat,
    runs: chat.runs.slice(-MAX_STORED_RUNS),
  }));
  try {
    window.localStorage.setItem(CHATS_KEY, JSON.stringify({ chats: trimmed, activeId }));
  } catch {
    // A full quota loses persistence, not the live session.
  }
}

export function loadFolders(): string[] {
  try {
    const raw = window.localStorage.getItem(FOLDERS_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : [];
  } catch {
    return [];
  }
}

export function saveFolders(folders: string[]): void {
  try {
    window.localStorage.setItem(FOLDERS_KEY, JSON.stringify(folders));
  } catch {
    // A full quota loses persistence, not the live session.
  }
}

// The highest run id across every stored chat, so a reloaded session keeps
// allocating unique ids.
export function maxRunId(chats: Chat[]): number {
  let max = 0;
  for (const chat of chats) {
    for (const run of chat.runs) {
      if (run.id > max) {
        max = run.id;
      }
    }
  }
  return max;
}

// The short display name of a folder path: its last path segment, or the
// whole path when it has a single segment.
export function folderLabel(folder: string | null): string {
  if (!folder) {
    return "default dataset";
  }
  const normalized = folder.replace(/\\/g, "/").replace(/\/+$/, "");
  return normalized.split("/").pop() || folder;
}
