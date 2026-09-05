"use client";

import { useEffect, useState, type FormEvent } from "react";
import { getStatus, submitQuery } from "./lib/api";
import type { QueryResponse, StatusStep } from "./lib/types";
import { AnswerWithCitations } from "./components/AnswerWithCitations";
import { ChartPanel } from "./components/ChartPanel";
import { CitationPanel } from "./components/CitationPanel";
import { ProgressStepper } from "./components/ProgressStepper";
import { TracePanel } from "./components/TracePanel";

const EXAMPLE_QUERY =
  "How do PJM's interconnection queue rules compare to CAISO's for solar-plus-storage projects, and what was CAISO's solar generation trend last July?";

const POLL_INTERVAL_MS = 1200;

export default function Home() {
  const [query, setQuery] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [statusSteps, setStatusSteps] = useState<StatusStep[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [activeMarker, setActiveMarker] = useState<string | null>(null);

  // /query hands back a run_id immediately (the graph itself runs as a
  // background task, ~15-30s) -- poll /status/{run_id} until it flips
  // done=true, updating the stepper's `statusSteps` along the way.
  useEffect(() => {
    if (!runId || !running) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        const status = await getStatus(runId);
        if (cancelled) return;
        setStatusSteps(status.steps);
        if (status.done) {
          setResult(status.result);
          setRunning(false);
          clearInterval(interval);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setRunning(false);
        clearInterval(interval);
      }
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [runId, running]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!query.trim() || running) return;
    setError(null);
    setActiveMarker(null);
    setResult(null);
    setStatusSteps([]);
    setRunId(null);
    setRunning(true);
    try {
      const { run_id } = await submitQuery(query);
      setRunId(run_id); // triggers the polling effect above
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRunning(false);
    }
  }

  function handleMarkerClick(marker: string) {
    setActiveMarker(marker);
    document
      .getElementById(`citation-${marker}`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  return (
    <div className="min-h-full flex-1 bg-zinc-50 dark:bg-zinc-950">
      <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-6 py-10">
        <header>
          <h1 className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">GridPulse AI</h1>
          <p className="text-sm text-zinc-500">
            Multi-agent RAG over grid interconnection/policy documents + EIA generation-mix
            data.
          </p>
        </header>

        <form onSubmit={handleSubmit} className="flex flex-col gap-2 sm:flex-row">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={EXAMPLE_QUERY}
            className="flex-1 rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm text-zinc-900 shadow-sm focus:border-blue-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
          />
          <button
            type="submit"
            disabled={running || !query.trim()}
            className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {running ? "Running agents…" : "Ask"}
          </button>
        </form>

        {running && !result && <ProgressStepper steps={statusSteps} />}

        {error && (
          <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
            {error}
          </p>
        )}

        {result && (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <section className="flex flex-col gap-6 lg:col-span-2">
              <div className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
                <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                  Answer
                </h2>
                <AnswerWithCitations
                  answer={result.answer}
                  citations={result.citations}
                  onMarkerClick={handleMarkerClick}
                  activeMarker={activeMarker}
                />
              </div>

              {result.chart_spec && (
                <div className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
                  <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Chart
                  </h2>
                  <ChartPanel spec={result.chart_spec} />
                </div>
              )}

              <div className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
                <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                  Trajectory tracer
                </h2>
                <TracePanel runId={result.run_id} />
              </div>
            </section>

            <aside className="flex flex-col gap-3">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                Citations
              </h2>
              <CitationPanel
                citations={result.citations}
                activeMarker={activeMarker}
                onSelect={setActiveMarker}
              />
            </aside>
          </div>
        )}
      </div>
    </div>
  );
}
