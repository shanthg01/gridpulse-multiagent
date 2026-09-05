"""Hybrid retrieval: dense (pgvector cosine) + sparse (Postgres tsvector/BM25-ish)
fused via Reciprocal Rank Fusion (RRF).

Usage:
    from rag.hybrid_search import hybrid_search
    hits = hybrid_search("How does PJM's interconnection cluster study work?")
"""

from __future__ import annotations

import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from rag.embed import embed_query

load_dotenv(override=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://gridpulse:gridpulse@localhost:5433/gridpulse"
)

RRF_K = 60  # standard RRF damping constant
CANDIDATE_POOL = 40  # how many candidates to pull from each ranking before fusion


def _dense_candidates(cur, query_vec: list[float], limit: int) -> list[tuple[int, int]]:
    """Returns [(chunk_id, rank)] ordered by cosine distance (rank 1 = closest)."""
    cur.execute(
        """
        SELECT id
        FROM chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (query_vec, limit),
    )
    return [(row[0], i + 1) for i, row in enumerate(cur.fetchall())]


def _sparse_candidates(cur, query_text: str, limit: int) -> list[tuple[int, int]]:
    """Returns [(chunk_id, rank)] ordered by ts_rank_cd (rank 1 = most relevant)."""
    cur.execute(
        """
        SELECT id
        FROM chunks
        WHERE tsv @@ plainto_tsquery('english', %s)
        ORDER BY ts_rank_cd(tsv, plainto_tsquery('english', %s)) DESC
        LIMIT %s
        """,
        (query_text, query_text, limit),
    )
    return [(row[0], i + 1) for i, row in enumerate(cur.fetchall())]


def _fuse(dense: list[tuple[int, int]], sparse: list[tuple[int, int]]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for chunk_id, rank in dense:
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    for chunk_id, rank in sparse:
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    return scores


def hybrid_search(query: str, top_k: int = 8, iso: str | None = None) -> list[dict]:
    """Returns top_k fused hits: [{chunk_id, document_id, doc_title, iso, page, bbox,
    text, dense_rank, sparse_rank, rrf_score}], best first.

    `iso` optionally filters candidates to one ISO's documents post-fusion (small
    corpus, so filtering after fusion rather than push down is fine).
    """
    query_vec = embed_query(query)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            dense = _dense_candidates(cur, query_vec, CANDIDATE_POOL)
            sparse = _sparse_candidates(cur, query, CANDIDATE_POOL)
            fused = _fuse(dense, sparse)

            if not fused:
                return []

            dense_rank = dict(dense)
            sparse_rank = dict(sparse)

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur2:
                cur2.execute(
                    """
                    SELECT c.id AS chunk_id, c.document_id, c.page, c.bbox, c.text,
                           d.title AS doc_title, d.iso, d.source_type
                    FROM chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE c.id = ANY(%s)
                    """,
                    (list(fused.keys()),),
                )
                rows = {r["chunk_id"]: r for r in cur2.fetchall()}

        hits = []
        for chunk_id, score in fused.items():
            row = rows.get(chunk_id)
            if row is None:
                continue
            if iso and (row.get("iso") or "").upper() != iso.upper():
                continue
            hits.append(
                {
                    **row,
                    "dense_rank": dense_rank.get(chunk_id),
                    "sparse_rank": sparse_rank.get(chunk_id),
                    "rrf_score": score,
                }
            )
        hits.sort(key=lambda h: h["rrf_score"], reverse=True)
        return hits[:top_k]
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Ad-hoc hybrid search against the chunks table.")
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--iso", default=None)
    args = parser.parse_args()

    results = hybrid_search(args.query, top_k=args.top_k, iso=args.iso)
    for r in results:
        preview = r["text"][:200].replace("\n", " ")
        print(
            f"[{r['rrf_score']:.4f}] doc={r['doc_title'][:60]!r} "
            f"page={r['page']} dense={r['dense_rank']} sparse={r['sparse_rank']}\n"
            f"    {preview}...\n"
        )
