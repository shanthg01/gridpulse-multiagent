"""Chunk + embed all documents in the corpus, load into `chunks` table.

Usage:
    python -m rag.ingest_chunks              # process all docs (skips already-chunked)
    python -m rag.ingest_chunks --reembed     # re-chunk/re-embed even if chunks exist
"""

from __future__ import annotations

import argparse
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from ingestion.doc_corpus import fetch_all
from rag.chunking import chunk_pdf
from rag.embed import embed_passages

load_dotenv(override=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://gridpulse:gridpulse@localhost:5433/gridpulse"
)
EMBED_BATCH_SIZE = 32


def _has_chunks(conn, document_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM chunks WHERE document_id = %s LIMIT 1", (document_id,))
        return cur.fetchone() is not None


def _insert_chunks(conn, document_id: int, chunks: list[dict], embeddings: list[list[float]]) -> None:
    records = [
        (document_id, c["page"], psycopg2.extras.Json(c["bbox"]), c["text"], emb)
        for c, emb in zip(chunks, embeddings)
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO chunks (document_id, page, bbox, text, embedding)
            VALUES %s
            """,
            records,
            template="(%s, %s, %s, %s, %s::vector)",
        )
    conn.commit()


def run(reembed: bool = False) -> None:
    docs = fetch_all()  # idempotent: skips re-download, returns (doc_id, local_path) for all
    conn = psycopg2.connect(DATABASE_URL)
    try:
        for doc_id, path in docs:
            if not reembed and _has_chunks(conn, doc_id):
                print(f"[ingest_chunks] doc_id={doc_id} already chunked, skipping")
                continue

            if reembed:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                conn.commit()

            print(f"[ingest_chunks] chunking doc_id={doc_id} ({path.name})")
            chunks = chunk_pdf(str(path))
            if not chunks:
                print(f"[ingest_chunks] doc_id={doc_id}: no extractable text, skipping")
                continue

            print(f"[ingest_chunks] doc_id={doc_id}: {len(chunks)} chunks, embedding...")
            embeddings: list[list[float]] = []
            for i in range(0, len(chunks), EMBED_BATCH_SIZE):
                batch = chunks[i : i + EMBED_BATCH_SIZE]
                embeddings.extend(embed_passages([c["text"] for c in batch]))

            _insert_chunks(conn, doc_id, chunks, embeddings)
            print(f"[ingest_chunks] doc_id={doc_id}: inserted {len(chunks)} chunks")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chunk + embed the doc corpus into Postgres.")
    parser.add_argument("--reembed", action="store_true", help="re-chunk/re-embed docs that already have chunks")
    args = parser.parse_args()
    run(reembed=args.reembed)
