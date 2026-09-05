"use client";

import { useEffect, useState } from "react";
import type { RouterDecision, StatusStep } from "../lib/types";

const STAGES: { key: string; label: string }[] = [
  { key: "router", label: "Classifying query" },
  { key: "rag_agent", label: "Searching regulatory documents" },
  { key: "timeseries_agent", label: "Querying grid generation data" },
  { key: "synthesis_agent", label: "Synthesizing answer" },
];

type StageStatus = "done" | "active" | "pending" | "skipped";

function deriveStageStatuses(steps: StatusStep[]): StageStatus[] {
  const doneAgents = new Set(steps.map((s) => s.agent_name));
  const router = steps.find((s) => s.agent_name === "router");
  const decision = router?.output as RouterDecision | undefined;

  let seenActive = false;
  return STAGES.map((stage) => {
    if (doneAgents.has(stage.key)) return "done";

    const skippedByRouter =
      decision != null &&
      ((stage.key === "rag_agent" && !decision.needs_rag) ||
        (stage.key === "timeseries_agent" && !decision.needs_timeseries));
    if (skippedByRouter) return "skipped";

    if (!seenActive) {
      seenActive = true;
      return "active";
    }
    return "pending";
  });
}

const DOT_CLASSES: Record<StageStatus, string> = {
  done: "bg-emerald-500",
  active: "bg-blue-600 animate-pulse",
  pending: "bg-zinc-300 dark:bg-zinc-700",
  skipped: "bg-zinc-200 dark:bg-zinc-800",
};

const LABEL_CLASSES: Record<StageStatus, string> = {
  done: "text-zinc-900 dark:text-zinc-100",
  active: "text-blue-700 dark:text-blue-400 font-medium",
  pending: "text-zinc-400 dark:text-zinc-600",
  skipped: "text-zinc-300 line-through dark:text-zinc-700",
};

/** Live stage-by-stage progress while a query's background run is in
 * flight. `steps` grows as agent_steps rows land (see /status polling in
 * page.tsx) -- router/rag/timeseries/synthesis, in the graph's fixed order.
 * rag/timeseries show as "skipped" once the router step reveals the query
 * didn't need that branch. */
export function ProgressStepper({ steps }: { steps: StatusStep[] }) {
  const statuses = deriveStageStatuses(steps);
  const [elapsedSec, setElapsedSec] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => setElapsedSec(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Running agents
        </h2>
        <span className="text-xs text-zinc-400 tabular-nums">{elapsedSec}s</span>
      </div>
      <ol className="space-y-2.5">
        {STAGES.map((stage, i) => (
          <li key={stage.key} className="flex items-center gap-2.5 text-sm">
            <span
              className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT_CLASSES[statuses[i]]}`}
              aria-hidden
            />
            <span className={LABEL_CLASSES[statuses[i]]}>
              {stage.label}
              {statuses[i] === "skipped" && " (not needed for this query)"}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
