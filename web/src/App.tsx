import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { Dataset, DatasetTable, Meta, fetchDataset, fetchMeta, streamRun } from "./api";
import {
  Chat,
  loadChats,
  loadFolders,
  maxRunId,
  newChat,
  saveChats,
  saveFolders,
} from "./chat";
import { formatInt } from "./format";
import { RunCard } from "./RunCard";
import { Lightbox, Sheet } from "./Sheet";
import { ChatList, ChatsPanel, FolderPanel, InstrumentStrip, TableColumns } from "./Sidebar";
import { StatsModal } from "./StatsModal";
import { suggestQuestions } from "./suggest";
import { ThemeToggle } from "./ThemeToggle";
import {
  Run,
  applyAnswer,
  applyError,
  applyPlan,
  applyStopped,
  applyToolCall,
  applyToolResult,
  applyUsage,
  newRun,
  toggleExpanded,
} from "./run";

const FALLBACK_QUESTIONS = [
  "Which region dropped most in Q3?",
  "Chart monthly revenue by category",
  "Which regions missed their revenue target?",
];

const MAX_TITLE_LENGTH = 60;

type FilamentState = "idle" | "live" | "settle";

function chatTitle(question: string): string {
  return question.length <= MAX_TITLE_LENGTH
    ? question
    : `${question.slice(0, MAX_TITLE_LENGTH - 3)}...`;
}

export default function App() {
  const [stored] = useState(loadChats);
  const [chats, setChats] = useState<Chat[]>(stored.chats);
  const [activeId, setActiveId] = useState<string | null>(stored.activeId);
  const [folders, setFolders] = useState<string[]>(loadFolders);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [datasetError, setDatasetError] = useState<string | null>(null);
  const [datasetLoading, setDatasetLoading] = useState(false);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [filament, setFilament] = useState<FilamentState>("idle");
  const [sheetTable, setSheetTable] = useState<DatasetTable | null>(null);
  const [chatsOpen, setChatsOpen] = useState(false);
  const [statsOpen, setStatsOpen] = useState(false);
  const [zoom, setZoom] = useState<{ src: string; alt: string } | null>(null);
  const nextRunId = useRef(maxRunId(stored.chats) + 1);
  const abortRef = useRef<(() => void) | null>(null);
  const settleTimer = useRef<number | null>(null);
  const datasetFetch = useRef(0);
  const endRef = useRef<HTMLDivElement | null>(null);

  const activeChat = chats.find((chat) => chat.id === activeId) ?? null;
  const activeFolder = activeChat?.folder ?? null;
  const runs = activeChat?.runs ?? [];

  useEffect(() => {
    fetchMeta()
      .then(setMeta)
      .catch(() => setMeta(null));
    return () => abortRef.current?.();
  }, []);

  useEffect(() => {
    saveChats(chats, activeId);
  }, [chats, activeId]);

  useEffect(() => {
    saveFolders(folders);
  }, [folders]);

  const loadDataset = useCallback((folder: string | null) => {
    const ticket = ++datasetFetch.current;
    setDatasetLoading(true);
    setDatasetError(null);
    fetchDataset(folder)
      .then((value) => {
        if (ticket === datasetFetch.current) {
          setDataset(value);
        }
      })
      .catch((error: Error) => {
        if (ticket === datasetFetch.current) {
          setDataset(null);
          setDatasetError(error.message);
        }
      })
      .finally(() => {
        if (ticket === datasetFetch.current) {
          setDatasetLoading(false);
        }
      });
  }, []);

  useEffect(() => {
    loadDataset(activeFolder);
  }, [activeFolder, loadDataset]);

  useEffect(() => {
    if (busy) {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [chats, busy]);

  const updateRun = useCallback(
    (chatId: string, runId: number, transform: (run: Run) => Run) => {
      setChats((previous) =>
        previous.map((chat) =>
          chat.id === chatId
            ? {
                ...chat,
                runs: chat.runs.map((run) => (run.id === runId ? transform(run) : run)),
              }
            : chat,
        ),
      );
    },
    [],
  );

  const updateChat = useCallback((chatId: string, transform: (chat: Chat) => Chat) => {
    setChats((previous) =>
      previous.map((chat) => (chat.id === chatId ? transform(chat) : chat)),
    );
  }, []);

  const settle = useCallback(() => {
    setFilament("settle");
    if (settleTimer.current !== null) {
      window.clearTimeout(settleTimer.current);
    }
    settleTimer.current = window.setTimeout(() => setFilament("idle"), 1400);
  }, []);

  // Creates a chat inheriting the active chat's model and folder, activates
  // it, and returns it. The first chat starts on the default model and the
  // default data folder.
  const createChat = useCallback((): Chat => {
    const chat = newChat(activeChat?.model || meta?.model || "", activeChat?.folder ?? null);
    setChats((previous) => [...previous, chat]);
    setActiveId(chat.id);
    return chat;
  }, [activeChat, meta]);

  const deleteChat = (id: string) => {
    setChats((previous) => {
      const remaining = previous.filter((chat) => chat.id !== id);
      if (id === activeId) {
        setActiveId(remaining.length > 0 ? remaining[remaining.length - 1].id : null);
      }
      return remaining;
    });
  };

  const selectChat = (id: string) => {
    setActiveId(id);
    setChatsOpen(false);
  };

  const setChatFolder = (folder: string | null) => {
    const chat = activeChat ?? createChat();
    updateChat(chat.id, (value) => ({ ...value, folder }));
  };

  const addFolder = (path: string) => {
    const ticket = ++datasetFetch.current;
    setDatasetLoading(true);
    setDatasetError(null);
    fetchDataset(path)
      .then((value) => {
        setFolders((previous) => (previous.includes(path) ? previous : [...previous, path]));
        if (ticket === datasetFetch.current) {
          setDataset(value);
          setDatasetLoading(false);
        }
        setChatFolder(path);
      })
      .catch((error: Error) => {
        if (ticket === datasetFetch.current) {
          setDatasetError(error.message);
          setDatasetLoading(false);
        }
      });
  };

  const ask = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) {
      return;
    }
    const chat = activeChat ?? createChat();
    const model = chat.model || meta?.model || "";
    const id = nextRunId.current++;
    const run = newRun(id, trimmed, model, meta?.max_tool_rounds ?? 12);
    updateChat(chat.id, (value) => ({
      ...value,
      title: value.runs.length === 0 ? chatTitle(trimmed) : value.title,
      runs: [...value.runs, run],
    }));
    setBusy(true);
    setFilament("live");
    setQuestion("");

    const finish = () => {
      setBusy(false);
      abortRef.current = null;
      settle();
    };

    abortRef.current = streamRun(trimmed, model || null, chat.folder, {
      onUsage: (detail) => updateRun(chat.id, id, (r) => applyUsage(r, detail)),
      onPlan: (goal) => updateRun(chat.id, id, (r) => applyPlan(r, goal)),
      onToolCall: (tool, args) => updateRun(chat.id, id, (r) => applyToolCall(r, tool, args)),
      onToolResult: (detail) => updateRun(chat.id, id, (r) => applyToolResult(r, detail)),
      onAnswer: (answer, charts, usage) => {
        updateRun(chat.id, id, (r) => applyAnswer(r, answer, charts, usage));
        finish();
      },
      onError: (message) => {
        updateRun(chat.id, id, (r) => applyError(r, message));
        finish();
      },
    });
  };

  // Closes the event stream and marks the run stopped. The server finishes
  // its turn detached; for a local tool that is an acceptable trade for an
  // immediate way out of a long run.
  const stop = () => {
    abortRef.current?.();
    abortRef.current = null;
    setChats((previous) =>
      previous.map((chat) => ({
        ...chat,
        runs: chat.runs.map((run) => (run.status === "running" ? applyStopped(run) : run)),
      })),
    );
    setBusy(false);
    settle();
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    ask(question);
  };

  const setModel = (name: string) => {
    const chat = activeChat ?? createChat();
    updateChat(chat.id, (value) => ({ ...value, model: name }));
  };

  const activeRun = runs.length > 0 ? runs[runs.length - 1] : null;
  const progress =
    filament === "settle"
      ? 1
      : busy && activeRun && activeRun.status === "running"
        ? Math.max(0.05, activeRun.round / activeRun.maxRounds)
        : 0;

  const suggestions =
    dataset && dataset.tables.length > 0 ? suggestQuestions(dataset) : FALLBACK_QUESTIONS;

  return (
    <div className="app">
      <header className="topbar">
        <div className="wordmark">
          <span className="wordmark-glyph" aria-hidden="true" />
          Insight Agent
        </div>
        <div className="topbar-controls">
          {meta && !meta.api_key_configured && (
            <span className="key-warning" role="status">
              no API key configured
            </span>
          )}
          {meta && (
            <label className="model-select">
              <span className="eyebrow">model</span>
              <select
                value={activeChat?.model || meta.model}
                onChange={(event) => setModel(event.target.value)}
                disabled={busy}
              >
                {meta.models.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button type="button" className="stats-button" onClick={() => setStatsOpen(true)}>
            stats
          </button>
          <ThemeToggle />
        </div>
      </header>
      <div
        className={`filament is-${filament}`}
        role="progressbar"
        aria-label="Run progress"
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={Math.round(progress * 100) / 100}
      >
        <span style={{ transform: `scaleX(${progress})` }} />
      </div>

      <InstrumentStrip
        activeChat={activeChat}
        dataset={dataset}
        onOpenChats={() => setChatsOpen(true)}
        onOpenTable={setSheetTable}
      />

      <div className="body">
        <aside className="sidebar">
          <ChatsPanel
            chats={chats}
            activeId={activeId}
            onSelect={selectChat}
            onDelete={deleteChat}
            onNew={createChat}
          />
          <FolderPanel
            folder={activeFolder}
            folders={folders}
            dataset={dataset}
            error={datasetError}
            loading={datasetLoading}
            onPickFolder={setChatFolder}
            onAddFolder={addFolder}
            onReload={() => loadDataset(activeFolder)}
          />
        </aside>

        <main className="main">
          <div className="runs">
            {runs.length === 0 && (
              <div className="empty">
                <h2>On the desk</h2>
                <p className="empty-note">
                  {dataset && dataset.tables.length > 0
                    ? "These tables are loaded. Ask a question and watch the agent reason over them, live."
                    : "The agent inspects the schema, runs read-only SQL, and draws charts over MCP."}
                </p>
                {dataset && dataset.tables.length > 0 && (
                  <div className="table-cards">
                    {dataset.tables.map((table) => (
                      <button
                        key={table.table}
                        type="button"
                        className="table-card"
                        onClick={() => setSheetTable(table)}
                      >
                        <span className="table-card-name">{table.table}</span>
                        <span className="table-card-rows">
                          {formatInt(table.row_count)} rows
                        </span>
                        <span className="table-card-columns">
                          {table.columns.slice(0, 8).map((column) => (
                            <span key={column.name} className="value-chip">
                              {column.name}
                            </span>
                          ))}
                          {table.columns.length > 8 && (
                            <span className="value-chip">
                              +{table.columns.length - 8} more
                            </span>
                          )}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
                <span className="eyebrow">try asking</span>
                <div className="example-row">
                  {suggestions.map((example) => (
                    <button
                      key={example}
                      type="button"
                      className="example"
                      onClick={() => ask(example)}
                      disabled={busy || !meta}
                    >
                      {example}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {runs.map((run) => (
              <RunCard
                key={run.id}
                run={run}
                onToggle={(id) =>
                  activeChat && updateRun(activeChat.id, id, toggleExpanded)
                }
                onZoom={(src, alt) => setZoom({ src, alt })}
              />
            ))}
            <div ref={endRef} />
          </div>

          <form className="composer" onSubmit={onSubmit}>
            <div className="composer-pill">
              <input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Ask a question about the dataset"
                aria-label="Question"
                disabled={busy}
                autoComplete="off"
              />
              {busy ? (
                <button type="button" className="composer-stop" onClick={stop}>
                  Stop
                </button>
              ) : (
                <button type="submit" disabled={!question.trim()}>
                  Ask
                </button>
              )}
            </div>
          </form>
        </main>
      </div>

      {sheetTable && (
        <Sheet title={sheetTable.table} onClose={() => setSheetTable(null)}>
          <TableColumns table={sheetTable} />
        </Sheet>
      )}
      {chatsOpen && (
        <Sheet title="Chats" onClose={() => setChatsOpen(false)}>
          <button
            type="button"
            className="new-chat"
            onClick={() => {
              createChat();
              setChatsOpen(false);
            }}
          >
            New chat
          </button>
          <ChatList
            chats={chats}
            activeId={activeId}
            onSelect={selectChat}
            onDelete={deleteChat}
          />
        </Sheet>
      )}
      {statsOpen && (
        <Sheet title="Usage" onClose={() => setStatsOpen(false)}>
          <StatsModal chats={chats} />
        </Sheet>
      )}
      {zoom && <Lightbox src={zoom.src} alt={zoom.alt} onClose={() => setZoom(null)} />}
    </div>
  );
}
