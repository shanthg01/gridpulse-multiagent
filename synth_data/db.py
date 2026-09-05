"""Shared Postgres helpers for the synthetic data foundry.

Mirrors the connection pattern used in rag/hybrid_search.py and
agents/timeseries_agent.py: `load_dotenv(override=True)` because a global
env var on this machine otherwise shadows DATABASE_URL.
"""

from __future__ import annotations

import os
import random

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(override=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://gridpulse:gridpulse@localhost:5433/gridpulse"
)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def sample_chunks(n: int, min_len: int = 200, seed: int | None = None) -> list[dict]:
    """Sample up to `n` chunks, spread across as many distinct documents as
    possible (round-robin over documents rather than a flat random sample, so
    a 1400-chunk document like FERC Order 2023 doesn't crowd out the small
    curtailment-report documents).

    Returns [{chunk_id, document_id, doc_title, iso, page, text}], skipping
    chunks shorter than `min_len` chars (too little context to ground a
    question on).
    """
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT c.id AS chunk_id, c.document_id, c.page, c.text,
                       d.title AS doc_title, d.iso
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE length(c.text) >= %s
                """,
                (min_len,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    by_doc: dict[int, list[dict]] = {}
    for row in rows:
        by_doc.setdefault(row["document_id"], []).append(row)

    rng = random.Random(seed)
    for bucket in by_doc.values():
        rng.shuffle(bucket)

    doc_ids = list(by_doc.keys())
    rng.shuffle(doc_ids)

    picked: list[dict] = []
    idx = 0
    while len(picked) < n and any(by_doc[d] for d in doc_ids):
        doc_id = doc_ids[idx % len(doc_ids)]
        bucket = by_doc[doc_id]
        if bucket:
            picked.append(bucket.pop())
        idx += 1

    return picked[:n]


def fetch_chunk(chunk_id: int) -> dict | None:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT c.id AS chunk_id, c.document_id, c.page, c.text,
                       d.title AS doc_title, d.iso
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.id = %s
                """,
                (chunk_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def insert_pair(
    source_chunk_id: int | None,
    prompt: str,
    chosen: str,
    rejected: str | None = None,
    judge_score: float | None = None,
    judge_rationale: str | None = None,
) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO synthetic_pairs
                    (source_chunk_id, prompt, chosen, rejected, judge_score, judge_rationale)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (source_chunk_id, prompt, chosen, rejected, judge_score, judge_rationale),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    finally:
        conn.close()
