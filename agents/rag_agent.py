"""RAG agent — hybrid search over the regulatory corpus, synthesized answer
with inline citation markers ([C1], [C2], ...) mapped back to chunk/page/bbox
for the frontend's PDF citation highlight.
"""

from __future__ import annotations

from agents.llm import AGENT_MODEL, LLMResult, call_claude
from rag.hybrid_search import hybrid_search

SYSTEM_PROMPT = """You are a grid-policy research assistant. Answer the user's
question using ONLY the numbered source excerpts provided below. Cite every
claim with its excerpt marker, e.g. [C1], [C3]. If the excerpts don't contain
enough information to answer, say so plainly rather than guessing."""


def _build_context(hits: list[dict]) -> tuple[str, list[dict]]:
    blocks = []
    citations = []
    for i, hit in enumerate(hits, start=1):
        marker = f"C{i}"
        blocks.append(f"[{marker}] ({hit['doc_title']}, p.{hit['page']})\n{hit['text']}")
        citations.append(
            {
                "marker": marker,
                "chunk_id": hit["chunk_id"],
                "document_id": hit["document_id"],
                "doc_title": hit["doc_title"],
                "page": hit["page"],
                "bbox": hit["bbox"],
                "rrf_score": hit["rrf_score"],
            }
        )
    return "\n\n".join(blocks), citations


def run_rag(query: str, iso: str | None = None, top_k: int = 6) -> dict:
    hits = hybrid_search(query, top_k=top_k, iso=iso)
    if not hits:
        return {
            "answer": "No relevant regulatory documents found in the corpus for this question.",
            "citations": [],
            "retrieval_score": 0.0,
            "llm_result": None,
        }

    context, citations = _build_context(hits)
    user_prompt = f"Question: {query}\n\nSource excerpts:\n\n{context}"

    result: LLMResult = call_claude(
        system=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=AGENT_MODEL,
        max_tokens=1536,
    )

    avg_score = sum(h["rrf_score"] for h in hits) / len(hits)
    return {
        "answer": result.text,
        "citations": citations,
        "retrieval_score": avg_score,
        "llm_result": result,
    }
