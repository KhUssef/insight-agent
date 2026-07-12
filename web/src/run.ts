// The client-side model of one agent run, folded together from the SSE
// events as they arrive.

import type { RunUsage } from "./api";

export type StepStatus = "running" | "done" | "error";

export interface PlanStep {
  kind: "plan";
  id: number;
  round: number;
  goal: string;
}

export interface ToolStep {
  kind: "tool";
  id: number;
  round: number;
  tool: string;
  args: Record<string, unknown>;
  status: StepStatus;
  startedAt: number;
  durationMs?: number;
  summary?: string;
  chartPath?: string;
}

export type Step = PlanStep | ToolStep;

export type RunStatus = "running" | "done" | "error" | "stopped";

export interface Run {
  id: number;
  question: string;
  model: string;
  status: RunStatus;
  startedAt: number;
  lastEventAt: number;
  round: number;
  maxRounds: number;
  expanded: boolean;
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  steps: Step[];
  answer?: string;
  charts: string[];
  usage?: RunUsage;
  error?: string;
}

let nextStepId = 1;

export function newRun(id: number, question: string, model: string, maxRounds: number): Run {
  const now = Date.now();
  return {
    id,
    question,
    model,
    status: "running",
    startedAt: now,
    lastEventAt: now,
    round: 0,
    maxRounds,
    expanded: true,
    steps: [],
    charts: [],
  };
}

export function applyUsage(run: Run, detail: Record<string, unknown>): Run {
  return {
    ...run,
    lastEventAt: Date.now(),
    round: (detail.round as number) ?? run.round,
    maxRounds: (detail.max_rounds as number) ?? run.maxRounds,
    promptTokens: (detail.prompt_tokens as number) ?? run.promptTokens,
    completionTokens: (detail.completion_tokens as number) ?? run.completionTokens,
    totalTokens: (detail.total_tokens as number) ?? run.totalTokens,
  };
}

export function applyPlan(run: Run, goal: string): Run {
  const step: PlanStep = { kind: "plan", id: nextStepId++, round: run.round, goal };
  return { ...run, lastEventAt: Date.now(), steps: [...run.steps, step] };
}

export function applyToolCall(run: Run, tool: string, args: Record<string, unknown>): Run {
  const step: ToolStep = {
    kind: "tool",
    id: nextStepId++,
    round: run.round,
    tool,
    args,
    status: "running",
    startedAt: Date.now(),
  };
  return { ...run, lastEventAt: Date.now(), steps: [...run.steps, step] };
}

export function applyToolResult(run: Run, detail: Record<string, unknown>): Run {
  const index = run.steps.findLastIndex(
    (step) => step.kind === "tool" && step.status === "running",
  );
  if (index < 0) {
    return run;
  }
  const step = run.steps[index] as ToolStep;
  const failed = typeof detail.error === "string";
  const updated: ToolStep = {
    ...step,
    status: failed ? "error" : "done",
    durationMs: Date.now() - step.startedAt,
    summary: (detail.summary as string) ?? (failed ? (detail.error as string) : "done"),
    chartPath: detail.path as string | undefined,
  };
  const steps = [...run.steps];
  steps[index] = updated;
  return { ...run, lastEventAt: Date.now(), steps };
}

export function applyAnswer(run: Run, answer: string, charts: string[], usage: RunUsage): Run {
  return { ...run, status: "done", expanded: false, answer, charts, usage };
}

export function applyError(run: Run, message: string): Run {
  return { ...run, status: "error", error: message };
}

export function applyStopped(run: Run): Run {
  return { ...run, status: "stopped" };
}

export function toggleExpanded(run: Run): Run {
  return { ...run, expanded: !run.expanded };
}

// True while the run is live and no tool dispatch is pending, i.e. the model
// itself is composing the next move.
export function isThinking(run: Run): boolean {
  if (run.status !== "running") {
    return false;
  }
  return !run.steps.some((step) => step.kind === "tool" && step.status === "running");
}
