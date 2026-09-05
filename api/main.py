"""FastAPI layer for GridPulse AI — see PLAN.md "API layer" / Phase 6.

Thin wrapper around the existing agent pipeline (agents.graph.run_query) and
Postgres tables (documents, agent_steps). Does not touch agents/, rag/, or
ingestion/ internals -- only imports their public entry points.

Run: uvicorn api.main:app --reload --port 8000  (from repo root, venv active)
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agents.graph import run_query
from ingestion.doc_corpus import DOC_MANIFEST

load_dotenv(override=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://gridpulse:gridpulse@localhost:5433/gridpulse"
)
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# documents.url -> local cached filename, same mapping doc_corpus.py used to
# populate data/raw/ in the first place (documents table doesn't store the
# filename itself, only the source url it was fetched from).
_URL_TO_FILENAME = {spec.url: spec.filename for spec in DOC_MANIFEST}

app = FastAPI(title="GridPulse AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_conn():
    return psycopg2.connect(DATABASE_URL)


class QueryRequest(BaseModel):
    query: str


@app.post("/query")
def post_query(req: QueryRequest):
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")
    return run_query(req.query)


@app.get("/trace/{run_id}")
def get_trace(run_id: str):
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT step_no, agent_name, tool_called, input, output,
                       tokens_in, tokens_out, latency_ms, retrieval_score,
                       created_at
                FROM agent_steps
                WHERE run_id = %s
                ORDER BY step_no
                """,
                (run_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        raise HTTPException(status_code=404, detail=f"no steps found for run_id={run_id}")
    return {"run_id": run_id, "steps": rows}


@app.get("/citations/{document_id}")
def get_citation(document_id: int):
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, source_type, iso, title, url, fetched_at
                FROM documents
                WHERE id = %s
                """,
                (document_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no document with id={document_id}")
    return row


@app.get("/documents/{document_id}/pdf")
def get_document_pdf(document_id: int):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT url, title FROM documents WHERE id = %s", (document_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no document with id={document_id}")

    url, title = row
    filename = _URL_TO_FILENAME.get(url)
    if filename is None:
        raise HTTPException(
            status_code=404,
            detail=f"document {document_id} has no known local cache filename",
        )

    path = RAW_DIR / filename
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"cached PDF not found on disk: {path}"
        )

    return FileResponse(path, media_type="application/pdf", filename=filename)
