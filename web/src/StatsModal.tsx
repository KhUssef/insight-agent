// Usage tables computed client-side from the persisted chats: one row per
// chat and one row per model, folded from each stored run's usage summary.

import { Chat } from "./chat";
import { formatInt, formatSeconds, formatTokens } from "./format";
import type { Run } from "./run";

interface ChatRow {
  title: string;
  runs: number;
  toolCalls: number;
  tokens: number;
  seconds: number;
}

interface ModelRow {
  model: string;
  runs: number;
  tokens: number;
  seconds: number;
}

function runModel(run: Run): string {
  return run.usage?.model || run.model || "unknown";
}

function chatRows(chats: Chat[]): ChatRow[] {
  return chats
    .filter((chat) => chat.runs.length > 0)
    .map((chat) => {
      const row: ChatRow = {
        title: chat.title,
        runs: chat.runs.length,
        toolCalls: 0,
        tokens: 0,
        seconds: 0,
      };
      for (const run of chat.runs) {
        row.toolCalls += run.usage?.tool_calls ?? 0;
        row.tokens += run.usage?.total_tokens ?? run.totalTokens ?? 0;
        row.seconds += run.usage?.duration_seconds ?? 0;
      }
      return row;
    });
}

function modelRows(chats: Chat[]): ModelRow[] {
  const byModel = new Map<string, ModelRow>();
  for (const chat of chats) {
    for (const run of chat.runs) {
      const model = runModel(run);
      const row = byModel.get(model) ?? { model, runs: 0, tokens: 0, seconds: 0 };
      row.runs += 1;
      row.tokens += run.usage?.total_tokens ?? run.totalTokens ?? 0;
      row.seconds += run.usage?.duration_seconds ?? 0;
      byModel.set(model, row);
    }
  }
  return [...byModel.values()].sort((a, b) => b.runs - a.runs);
}

export function StatsModal({ chats }: { chats: Chat[] }) {
  const perChat = chatRows(chats);
  const perModel = modelRows(chats);
  if (perChat.length === 0) {
    return <p className="panel-note">No runs yet. Ask a question first.</p>;
  }
  return (
    <div className="usage-tables">
      <section>
        <h3 className="panel-title">By chat</h3>
        <table className="usage-table">
          <thead>
            <tr>
              <th>chat</th>
              <th>runs</th>
              <th>tool calls</th>
              <th>tokens</th>
              <th>agent time</th>
            </tr>
          </thead>
          <tbody>
            {perChat.map((row) => (
              <tr key={row.title + row.runs}>
                <td className="usage-name">{row.title}</td>
                <td>{formatInt(row.runs)}</td>
                <td>{formatInt(row.toolCalls)}</td>
                <td>{formatTokens(row.tokens)}</td>
                <td>{formatSeconds(row.seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section>
        <h3 className="panel-title">By model</h3>
        <table className="usage-table">
          <thead>
            <tr>
              <th>model</th>
              <th>runs</th>
              <th>tokens</th>
              <th>avg time</th>
            </tr>
          </thead>
          <tbody>
            {perModel.map((row) => (
              <tr key={row.model}>
                <td className="usage-name">{row.model}</td>
                <td>{formatInt(row.runs)}</td>
                <td>{formatTokens(row.tokens)}</td>
                <td>{formatSeconds(row.runs > 0 ? row.seconds / row.runs : 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
