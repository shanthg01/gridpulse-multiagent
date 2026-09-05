"use client";

import { useEffect, useState } from "react";
import { getTrace } from "../lib/api";
import type { TraceStep } from "../lib/types";

/** Trajectory tracer panel: fetches /trace/{run_id} once a query completes
 * and renders one row per agent_steps row (agent name, tool, tokens, latency,
 * retrieval score). */
export function TracePanel({ runId }: { runId: string | null }) {
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    // Idiomatic fetch-on-effect loading flag -- the compiler's stricter
    // set-state-in-effect rule flags this, but there's no external system to
    // subscribe to here (a plain data fetch keyed on runId), so it's fine.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    getTrace(runId)
      .then((res) => {
        if (!cancelled) setSteps(res.steps);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (!runId) return null;
  if (loading) return <p className="text-sm text-zinc-500">Loading trace…</p>;
  if (error) return <p className="text-sm text-red-600 dark:text-red-400">Trace error: {error}</p>;
  if (steps.length === 0) return <p className="text-sm text-zinc-500">No trace steps found.</p>;

  return (
    <ol className="space-y-2">
      {steps.map((s) => (
        <li
          key={s.step_no}
          className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-zinc-900 px-2 py-0.5 text-xs font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900">
              step {s.step_no}
            </span>
            <span className="font-medium text-zinc-800 dark:text-zinc-100">{s.agent_name}</span>
            {s.tool_called && (
              <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                {s.tool_called}
              </span>
            )}
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
            <span>
              tokens in/out: {s.tokens_in ?? 0} / {s.tokens_out ?? 0}
            </span>
            <span>latency: {s.latency_ms ?? 0} ms</span>
            {s.retrieval_score != null && (
              <span>retrieval score: {s.retrieval_score.toFixed(4)}</span>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
