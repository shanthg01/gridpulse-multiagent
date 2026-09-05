// Shapes returned by the FastAPI backend (api/main.py), mirroring
// agents.graph.run_query()'s output and the agent_steps table.

export interface Citation {
  marker: string;
  chunk_id: number;
  document_id: number;
  doc_title: string;
  page: number | null;
  bbox: { x0: number; y0: number; x1: number; y1: number } | null;
  rrf_score: number;
}

export interface ChartEncodingField {
  field: string;
  type: string;
}

export interface ChartSpec {
  // "line" (trend, from timeseries_agent._summarize) or "arc" (share/pie).
  mark: string;
  encoding: Record<string, ChartEncodingField>;
  data: { values: Record<string, unknown>[] };
}

export interface QueryResponse {
  run_id: string;
  answer: string;
  citations: Citation[];
  chart_spec: ChartSpec | null;
}

export interface TraceStep {
  step_no: number;
  agent_name: string;
  tool_called: string | null;
  input: unknown;
  output: unknown;
  tokens_in: number | null;
  tokens_out: number | null;
  latency_ms: number | null;
  retrieval_score: number | null;
  created_at: string;
}

export interface TraceResponse {
  run_id: string;
  steps: TraceStep[];
}

export interface DocumentMeta {
  id: number;
  source_type: string;
  iso: string | null;
  title: string;
  url: string;
  fetched_at: string;
}
