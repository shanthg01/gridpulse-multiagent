"""Local dense embedding model — BAAI/bge-base-en-v1.5 (768-dim), zero API cost.

Swap EMBED_MODEL_NAME for text-embedding-3-large (OpenAI) if quality needs
outgrow the local model — remember to migrate chunks.embedding column dim
(1536 for OpenAI small, 3072 for large) if you do.
"""

from __future__ import annotations

from functools import lru_cache

EMBED_MODEL_NAME = "BAAI/bge-base-en-v1.5"
EMBED_DIM = 768

# bge models are trained w/ an instruction prefix for queries (not for the
# passages being indexed) — using it measurably improves retrieval quality.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL_NAME)


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Embed document chunks (no query prefix)."""
    if not texts:
        return []
    vectors = _model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    """Embed a search query (w/ bge instruction prefix)."""
    vectors = _model().encode(
        [QUERY_PREFIX + text], normalize_embeddings=True, show_progress_bar=False
    )
    return vectors[0].tolist()
