import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { chartUrl } from "./api";
import { formatSeconds, formatTokens } from "./format";
import { Run, Step, ToolStep, isThinking } from "./run";
import { SqlBlock } from "./Sql";

function chartBasename(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").pop() ?? normalized;
}

// True when the answer embeds this chart as a markdown image, which is the
// only case where showing it again below the text would duplicate it. A
// bare textual mention of the filename does not count.
function answerEmbedsChart(answer: string, path: string): boolean {
  const basename = chartBasename(path).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`!\\[[^\\]]*\\]\\([^)]*${basename}[^)]*\\)`).test(answer);
}

function formatStepDuration(ms: number | undefined): string | null {
  if (ms === undefined) {
    return null;
  }
  return `${(ms / 1000).toFixed(1)}s`;
}

function StepArgs({ step }: { step: ToolStep }) {
  const { args } = step;
  const sql = typeof args.sql === "string" ? args.sql : null;
  const rest = Object.entries(args).filter(([key]) => key !== "sql");
  if (!sql && rest.length === 0) {
    return null;
  }
  return (
    <div className="step-args">
      {sql && <SqlBlock sql={sql} />}
      {rest.length > 0 && (
        <dl className="arg-list">
          {rest.map(([key, value]) => (
            <div key={key} className="arg">
              <dt>{key}</dt>
              <dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function TraceStep({
  step,
  onZoom,
}: {
  step: Step;
  onZoom: (src: string, alt: string) => void;
}) {
  if (step.kind === "plan") {
    return (
      <li className="trace-step trace-plan">
        <span className="trace-node" aria-hidden="true" />
        <div className="trace-content">
          <span className="eyebrow">intent</span>
          <p className="plan-text">{step.goal}</p>
        </div>
      </li>
    );
  }
  const duration = formatStepDuration(step.durationMs);
  return (
    <li className={`trace-step trace-tool is-${step.status}`}>
      <span className="trace-node" aria-hidden="true" />
      <div className="trace-content">
        <div className="tool-head">
          <span className="tool-name">{step.tool}</span>
          <span className="tool-status">
            {step.status === "running" ? "running" : step.summary}
            {step.status !== "running" && duration && (
              <span className="tool-time"> in {duration}</span>
            )}
          </span>
        </div>
        <StepArgs step={step} />
        {step.chartPath && (
          <button
            type="button"
            className="chart-zoom"
            onClick={() =>
              onZoom(chartUrl(step.chartPath ?? ""), chartBasename(step.chartPath ?? ""))
            }
          >
            <img
              className="step-chart"
              src={chartUrl(step.chartPath)}
              alt={`Chart written by ${step.tool}`}
              loading="lazy"
            />
          </button>
        )}
      </div>
    </li>
  );
}

// Consecutive steps grouped by the LLM round that produced them, so the
// trace shows the loop structure and each gauge segment has a visible
// counterpart on the rail.
function groupByRound(steps: Step[]): { round: number; steps: Step[] }[] {
  const groups: { round: number; steps: Step[] }[] = [];
  for (const step of steps) {
    const last = groups[groups.length - 1];
    if (last && last.round === step.round) {
      last.steps.push(step);
    } else {
      groups.push({ round: step.round, steps: [step] });
    }
  }
  return groups;
}

function EffortGauge({ run }: { run: Run }) {
  const cells = Array.from({ length: run.maxRounds }, (_, index) => index < run.round);
  return (
    <div
      className="effort"
      role="img"
      aria-label={`${run.round} of ${run.maxRounds} rounds used`}
    >
      <span className="eyebrow">effort</span>
      <span className={`gauge ${run.status === "running" ? "is-live" : ""}`}>
        {cells.map((filled, index) => (
          <span key={index} className={`cell ${filled ? "is-filled" : ""}`} />
        ))}
      </span>
      <span
        className={`gauge-micro ${run.status === "running" ? "is-live" : ""}`}
        aria-hidden="true"
      >
        <span style={{ width: `${(run.round / run.maxRounds) * 100}%` }} />
      </span>
      <span className="gauge-label">
        {run.round}/{run.maxRounds} rounds
      </span>
    </div>
  );
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) {
      return;
    }
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

function receiptText(run: Run): string {
  const usage = run.usage ?? {};
  const parts = [
    `${usage.rounds ?? run.round} rounds`,
    `${usage.tool_calls ?? 0} tools`,
  ];
  if (usage.total_tokens !== undefined) {
    parts.push(`${formatTokens(usage.total_tokens)} tok`);
  }
  if (usage.duration_seconds !== undefined) {
    parts.push(formatSeconds(usage.duration_seconds));
  }
  return parts.join(" - ");
}

export function RunCard({
  run,
  onToggle,
  onZoom,
}: {
  run: Run;
  onToggle: (id: number) => void;
  onZoom: (src: string, alt: string) => void;
}) {
  const live = run.status === "running";
  const now = useNow(live);
  const elapsed =
    !live && run.usage?.duration_seconds !== undefined
      ? run.usage.duration_seconds
      : (now - run.startedAt) / 1000;
  const thinking = isThinking(run);
  const thinkingSeconds = (now - run.lastEventAt) / 1000;
  const answer = run.answer;
  const extraCharts =
    answer === undefined
      ? run.charts
      : run.charts.filter((path) => !answerEmbedsChart(answer, path));
  const collapsible = run.status === "done";
  const open = run.expanded || !collapsible;

  // The model references its charts inline as relative chart paths; rewrite
  // them to the /charts mount, make them zoomable, and drop any other image
  // source.
  const AnswerImage = (props: { src?: string; alt?: string }) => {
    const src = props.src ?? "";
    if (!src.replace(/\\/g, "/").includes("charts/")) {
      return null;
    }
    return (
      <button
        type="button"
        className="chart-zoom"
        onClick={() => onZoom(chartUrl(src), chartBasename(src))}
      >
        <img src={chartUrl(src)} alt={props.alt ?? "Chart produced by the agent"} />
      </button>
    );
  };

  return (
    <article className={`run run-${run.status}`}>
      <header className="run-head">
        <p className="question">{run.question}</p>
        <div className="run-meta">
          <span className="model-chip">{run.model}</span>
          <span className={`status-chip status-${run.status}`}>
            {live ? "working" : run.status}
          </span>
        </div>
      </header>

      {collapsible && (
        <button
          type="button"
          className="receipt"
          aria-expanded={open}
          onClick={() => onToggle(run.id)}
        >
          <span className={`disclosure ${open ? "is-open" : ""}`} aria-hidden="true" />
          <span className="receipt-gauge" aria-hidden="true">
            <span style={{ width: `${(run.round / run.maxRounds) * 100}%` }} />
          </span>
          <span className="receipt-text">{receiptText(run)}</span>
          <span className="receipt-hint">{open ? "hide trace" : "show trace"}</span>
        </button>
      )}

      <div className={`trace-fold ${open ? "is-open" : ""}`}>
        <div className="trace-fold-inner">
          {(run.steps.length > 0 || thinking) && (
            <ol className="trace">
              {groupByRound(run.steps).map((group) => (
                <li key={`round-${group.round}-${group.steps[0].id}`} className="trace-group">
                  <span className="round-tick" aria-hidden="true">
                    R{group.round}
                  </span>
                  <ol className="trace-round-steps">
                    {group.steps.map((step) => (
                      <TraceStep key={step.id} step={step} onZoom={onZoom} />
                    ))}
                  </ol>
                </li>
              ))}
              {thinking && (
                <li className="trace-group trace-thinking-group">
                  <span className="round-tick" aria-hidden="true">
                    R{run.round + 1}
                  </span>
                  <div className="trace-step trace-thinking">
                    <span className="trace-node" aria-hidden="true" />
                    <div className="trace-content">
                      <span className="thinking-label">
                        thinking
                        <span className="thinking-dashes" aria-hidden="true">
                          <span />
                          <span />
                          <span />
                        </span>
                        {thinkingSeconds >= 1.5 && (
                          <span className="thinking-time">
                            {formatSeconds(thinkingSeconds)}
                          </span>
                        )}
                      </span>
                    </div>
                  </div>
                </li>
              )}
            </ol>
          )}

          {(live || run.status === "stopped" || run.status === "error") && (
            <div className="run-footer">
              <EffortGauge run={run} />
              <div className="run-numbers">
                <span className="number">
                  <span className="eyebrow">tokens</span>
                  <span className="value">{formatTokens(run.totalTokens)}</span>
                </span>
                <span className="number">
                  <span className="eyebrow">time</span>
                  <span className="value">{formatSeconds(elapsed)}</span>
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      {answer !== undefined && (
        <section className="answer">
          <span className="eyebrow">answer</span>
          <div className="answer-text">
            <Markdown remarkPlugins={[remarkGfm]} components={{ img: AnswerImage }}>
              {answer}
            </Markdown>
          </div>
          {extraCharts.length > 0 && (
            <div className="answer-charts">
              {extraCharts.map((path) => (
                <button
                  key={path}
                  type="button"
                  className="chart-zoom"
                  onClick={() => onZoom(chartUrl(path), chartBasename(path))}
                >
                  <img src={chartUrl(path)} alt="Chart produced by the agent" />
                </button>
              ))}
            </div>
          )}
        </section>
      )}

      {run.error && (
        <section className="run-error" role="alert">
          <span className="eyebrow">error</span>
          <p>{run.error}</p>
        </section>
      )}

      {run.status === "stopped" && (
        <section className="run-stopped">
          <p>Stopped before the agent finished.</p>
        </section>
      )}
    </article>
  );
}
