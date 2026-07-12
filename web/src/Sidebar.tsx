import { FormEvent, useState } from "react";

import { Dataset, DatasetColumn, DatasetTable } from "./api";
import { Chat, folderLabel } from "./chat";
import { formatInt } from "./format";

function ColumnHint({ column }: { column: DatasetColumn }) {
  if (column.distinct_values && column.distinct_values.length > 0) {
    return (
      <span className="column-hint">
        {column.distinct_values.slice(0, 6).map((value) => (
          <span key={String(value)} className="value-chip">
            {String(value)}
          </span>
        ))}
      </span>
    );
  }
  if (column.min !== undefined && column.max !== undefined) {
    return (
      <span className="column-hint range">
        {String(column.min)} to {String(column.max)}
      </span>
    );
  }
  return null;
}

// The column detail for one table, shared by the sidebar accordion, the
// landing cards, and the narrow-layout bottom sheet.
export function TableColumns({ table }: { table: DatasetTable }) {
  return (
    <ul className="column-list">
      {table.columns.map((column) => (
        <li key={column.name} className="column">
          <div className="column-head">
            <span className="column-name">{column.name}</span>
            <span className="column-type">{column.type.toLowerCase()}</span>
          </div>
          <ColumnHint column={column} />
        </li>
      ))}
    </ul>
  );
}

function TableEntry({ table }: { table: DatasetTable }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="table-entry">
      <button
        type="button"
        className="table-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className={`disclosure ${open ? "is-open" : ""}`} aria-hidden="true" />
        <span className="table-name">{table.table}</span>
        <span className="table-rows">{formatInt(table.row_count)} rows</span>
      </button>
      {open && <TableColumns table={table} />}
    </li>
  );
}

// The chat list, shared by the sidebar and the narrow-layout bottom sheet.
export function ChatList({
  chats,
  activeId,
  onSelect,
  onDelete,
}: {
  chats: Chat[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  if (chats.length === 0) {
    return <p className="panel-note">No chats yet.</p>;
  }
  return (
    <ul className="chat-list">
      {chats.map((chat) => (
        <li key={chat.id} className={`chat-entry ${chat.id === activeId ? "is-active" : ""}`}>
          <button
            type="button"
            className="chat-open"
            onClick={() => onSelect(chat.id)}
            aria-current={chat.id === activeId}
          >
            <span className="chat-title">{chat.title}</span>
            <span className="chat-detail">
              {folderLabel(chat.folder)}
              {chat.runs.length > 0 && ` - ${formatInt(chat.runs.length)} runs`}
            </span>
          </button>
          <button
            type="button"
            className="chat-delete"
            aria-label={`Delete chat ${chat.title}`}
            onClick={() => onDelete(chat.id)}
          >
            delete
          </button>
        </li>
      ))}
    </ul>
  );
}

export function ChatsPanel({
  chats,
  activeId,
  onSelect,
  onDelete,
  onNew,
}: {
  chats: Chat[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <section className="panel">
      <button type="button" className="new-chat" onClick={onNew}>
        New chat
      </button>
      <ChatList chats={chats} activeId={activeId} onSelect={onSelect} onDelete={onDelete} />
    </section>
  );
}

// The active chat's data folder: its path, an input to add another folder
// (the server is local, so a pasted path is enough), a load-and-convert
// action, the resulting tables, and any files the scan skipped.
export function FolderPanel({
  folder,
  folders,
  dataset,
  error,
  loading,
  onPickFolder,
  onAddFolder,
  onReload,
}: {
  folder: string | null;
  folders: string[];
  dataset: Dataset | null;
  error: string | null;
  loading: boolean;
  onPickFolder: (folder: string | null) => void;
  onAddFolder: (path: string) => void;
  onReload: () => void;
}) {
  const [path, setPath] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = path.trim();
    if (!trimmed) {
      return;
    }
    onAddFolder(trimmed);
    setPath("");
  };

  const known: (string | null)[] = [null, ...folders];

  return (
    <section className="panel">
      <h2 className="panel-title">Data folder</h2>
      <p className="folder-path" title={folder ?? undefined}>
        {folder ?? "default dataset"}
      </p>
      <div className="folder-known">
        {known.map((entry) => (
          <button
            key={entry ?? ""}
            type="button"
            className={`folder-chip ${entry === folder ? "is-active" : ""}`}
            onClick={() => onPickFolder(entry)}
            title={entry ?? "the server's configured data directory"}
          >
            {folderLabel(entry)}
          </button>
        ))}
      </div>
      <form className="folder-add" onSubmit={submit}>
        <input
          value={path}
          onChange={(event) => setPath(event.target.value)}
          placeholder="Add a folder path"
          aria-label="Folder path"
        />
        <button type="submit" disabled={!path.trim() || loading}>
          Add
        </button>
      </form>
      <button type="button" className="folder-reload" onClick={onReload} disabled={loading}>
        {loading ? "Loading..." : "Load and convert"}
      </button>
      {error && (
        <p className="panel-note folder-error" role="alert">
          {error}
        </p>
      )}
      {dataset === null ? (
        !error && <p className="panel-note">Loading tables...</p>
      ) : dataset.tables.length === 0 ? (
        <p className="panel-note">
          No tables in this folder. Drop CSV, Excel, JSON, or Parquet files into it
          and load again.
        </p>
      ) : (
        <ul className="table-list">
          {dataset.tables.map((table) => (
            <TableEntry key={table.table} table={table} />
          ))}
        </ul>
      )}
      {dataset && dataset.skipped && dataset.skipped.length > 0 && (
        <div className="folder-skipped">
          <span className="eyebrow">skipped</span>
          <ul className="skipped-list">
            {dataset.skipped.map((entry) => (
              <li key={entry.file}>
                <span className="skipped-file">{entry.file}</span>
                <span className="skipped-reason">{entry.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// The narrow-layout replacement for the sidebar: a chat picker chip plus one
// horizontal strip of table chips. Tapping the picker opens the chat list in
// a bottom sheet; tapping a table chip opens its column detail.
export function InstrumentStrip({
  activeChat,
  dataset,
  onOpenChats,
  onOpenTable,
}: {
  activeChat: Chat | null;
  dataset: Dataset | null;
  onOpenChats: () => void;
  onOpenTable: (table: DatasetTable) => void;
}) {
  return (
    <div className="strip" aria-label="Chats and dataset">
      <button type="button" className="strip-chip strip-chats" onClick={onOpenChats}>
        {activeChat ? activeChat.title : "Chats"}
        <span className="strip-rows">{folderLabel(activeChat?.folder ?? null)}</span>
      </button>
      <div className="strip-tables">
        {(dataset?.tables ?? []).map((table) => (
          <button
            key={table.table}
            type="button"
            className="strip-chip"
            onClick={() => onOpenTable(table)}
          >
            {table.table}
            <span className="strip-rows">{formatInt(table.row_count)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
