// Typed access to the FastAPI backend: the three metadata endpoints and the
// Server-Sent Events stream of one agent run.

export interface Meta {
  model: string;
  models: string[];
  base_url: string;
  max_tool_rounds: number;
  api_key_configured: boolean;
}

export interface DatasetColumn {
  name: string;
  type: string;
  distinct_values?: (string | number | null)[];
  min?: string | number | null;
  max?: string | number | null;
}

export interface DatasetTable {
  table: string;
  row_count: number;
  columns: DatasetColumn[];
}

export interface SkippedFile {
  file: string;
  reason: string;
}

export interface Dataset {
  folder?: string;
  tables: DatasetTable[];
  skipped?: SkippedFile[];
}

export interface Stats {
  questions: number;
  answers: number;
  errors: number;
  rounds: number;
  tool_calls: number;
  charts: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  duration_seconds: number;
}

export interface RunUsage {
  rounds?: number;
  max_rounds?: number;
  tool_calls?: number;
  duration_seconds?: number;
  model?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface EventPayload {
  text: string;
  detail: Record<string, unknown>;
}

export interface StreamHandlers {
  onUsage: (detail: Record<string, unknown>) => void;
  onPlan: (goal: string) => void;
  onToolCall: (tool: string, args: Record<string, unknown>) => void;
  onToolResult: (detail: Record<string, unknown>) => void;
  onAnswer: (answer: string, charts: string[], usage: RunUsage) => void;
  onError: (message: string) => void;
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    let message = `${url} returned ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      // Non-JSON error bodies keep the generic message.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export const fetchMeta = (): Promise<Meta> => getJson<Meta>("/meta");
export const fetchStats = (): Promise<Stats> => getJson<Stats>("/stats");

// Describes the tables of one data folder (the server's default when folder
// is null). The scan also converts any supported non-CSV files it finds, so
// this doubles as the "load and convert" action.
export function fetchDataset(folder: string | null = null): Promise<Dataset> {
  const params = new URLSearchParams();
  if (folder) {
    params.set("folder", folder);
  }
  const query = params.toString();
  return getJson<Dataset>(query ? `/dataset?${query}` : "/dataset");
}

function parsePayload(event: Event): EventPayload | null {
  const data = (event as MessageEvent).data;
  if (typeof data !== "string") {
    return null;
  }
  try {
    return JSON.parse(data) as EventPayload;
  } catch {
    return null;
  }
}

// Opens the SSE stream for one question and dispatches each named event to
// its handler. Returns a function that aborts the stream. The stream closes
// itself after the answer or error event; a transport failure with no server
// payload is reported as a connection error.
export function streamRun(
  question: string,
  model: string | null,
  folder: string | null,
  handlers: StreamHandlers,
): () => void {
  const params = new URLSearchParams({ question });
  if (model) {
    params.set("model", model);
  }
  if (folder) {
    params.set("folder", folder);
  }
  const source = new EventSource(`/ask/stream?${params.toString()}`);
  let settled = false;

  const settle = () => {
    settled = true;
    source.close();
  };

  source.addEventListener("usage", (event) => {
    const payload = parsePayload(event);
    if (payload) {
      handlers.onUsage(payload.detail);
    }
  });

  source.addEventListener("plan", (event) => {
    const payload = parsePayload(event);
    if (payload) {
      handlers.onPlan((payload.detail.goal as string) ?? payload.text);
    }
  });

  source.addEventListener("tool_call", (event) => {
    const payload = parsePayload(event);
    if (payload) {
      handlers.onToolCall(
        (payload.detail.tool as string) ?? "tool",
        (payload.detail.arguments as Record<string, unknown>) ?? {},
      );
    }
  });

  source.addEventListener("tool_result", (event) => {
    const payload = parsePayload(event);
    if (payload) {
      handlers.onToolResult(payload.detail);
    }
  });

  source.addEventListener("answer", (event) => {
    const payload = parsePayload(event);
    if (payload) {
      handlers.onAnswer(
        (payload.detail.answer as string) ?? payload.text,
        (payload.detail.charts as string[]) ?? [],
        (payload.detail.usage as RunUsage) ?? {},
      );
    }
    settle();
  });

  // The server's own "error" events carry a payload; the EventSource
  // transport also fires "error" with no data when the connection drops.
  source.addEventListener("error", (event) => {
    if (settled) {
      return;
    }
    const payload = parsePayload(event);
    if (payload) {
      handlers.onError(payload.text || "The run failed.");
      settle();
    } else if (source.readyState === EventSource.CLOSED) {
      handlers.onError("Connection lost before the run finished.");
      settle();
    }
  });

  return settle;
}

export function chartUrl(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const basename = normalized.split("/").pop() ?? normalized;
  return `/charts/${encodeURIComponent(basename)}`;
}
