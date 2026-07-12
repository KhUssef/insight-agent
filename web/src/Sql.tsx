import { ReactNode, useState } from "react";

// A minimal SQL presenter: a hand-rolled tokenizer good enough for the
// SELECT statements the agent writes (keywords, strings, numbers, comments),
// plus a copy-to-clipboard affordance. No grammar, no dependency.

const KEYWORDS = new Set([
  "select",
  "from",
  "where",
  "group",
  "order",
  "by",
  "limit",
  "offset",
  "join",
  "left",
  "right",
  "inner",
  "outer",
  "full",
  "cross",
  "on",
  "as",
  "and",
  "or",
  "not",
  "in",
  "is",
  "null",
  "case",
  "when",
  "then",
  "else",
  "end",
  "with",
  "having",
  "union",
  "all",
  "distinct",
  "between",
  "like",
  "ilike",
  "asc",
  "desc",
  "over",
  "partition",
  "filter",
  "using",
]);

const FUNCTIONS = new Set([
  "sum",
  "count",
  "avg",
  "min",
  "max",
  "round",
  "cast",
  "coalesce",
  "extract",
  "date_trunc",
  "strftime",
  "concat",
  "lower",
  "upper",
  "abs",
  "row_number",
  "rank",
  "lag",
  "lead",
]);

const TOKEN_RE = /('(?:[^']|'')*'?)|(--[^\n]*)|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_][A-Za-z0-9_]*\b)/g;

function highlight(sql: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const match of sql.matchAll(TOKEN_RE)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      nodes.push(sql.slice(cursor, index));
    }
    const [text, str, comment, num, word] = match;
    if (str !== undefined) {
      nodes.push(
        <span key={key++} className="tok-str">
          {text}
        </span>,
      );
    } else if (comment !== undefined) {
      nodes.push(
        <span key={key++} className="tok-com">
          {text}
        </span>,
      );
    } else if (num !== undefined) {
      nodes.push(
        <span key={key++} className="tok-num">
          {text}
        </span>,
      );
    } else if (word !== undefined && KEYWORDS.has(word.toLowerCase())) {
      nodes.push(
        <span key={key++} className="tok-kw">
          {text}
        </span>,
      );
    } else if (word !== undefined && FUNCTIONS.has(word.toLowerCase())) {
      nodes.push(
        <span key={key++} className="tok-fn">
          {text}
        </span>,
      );
    } else {
      nodes.push(text);
    }
    cursor = index + text.length;
  }
  if (cursor < sql.length) {
    nodes.push(sql.slice(cursor));
  }
  return nodes;
}

export function SqlBlock({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard
      .writeText(sql)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {});
  };
  return (
    <div className="sql-block">
      <pre className="sql">{highlight(sql)}</pre>
      <button type="button" className="sql-copy" onClick={copy}>
        {copied ? "copied" : "copy"}
      </button>
    </div>
  );
}
