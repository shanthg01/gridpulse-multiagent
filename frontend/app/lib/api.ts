import type { DocumentMeta, QueryResponse, TraceResponse } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${body ? `: ${body}` : ""}`);
  }
  return res.json() as Promise<T>;
}

export async function postQuery(query: string): Promise<QueryResponse> {
  const res = await fetch(`${API_URL}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  return handle<QueryResponse>(res);
}

export async function getTrace(runId: string): Promise<TraceResponse> {
  const res = await fetch(`${API_URL}/trace/${runId}`);
  return handle<TraceResponse>(res);
}

export async function getDocument(documentId: number): Promise<DocumentMeta> {
  const res = await fetch(`${API_URL}/citations/${documentId}`);
  return handle<DocumentMeta>(res);
}

export function pdfUrl(documentId: number): string {
  return `${API_URL}/documents/${documentId}/pdf`;
}
