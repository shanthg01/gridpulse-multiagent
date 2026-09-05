"""SFT-style Q/A generation: samples chunks from the `chunks` table and
prompts an LLM to produce one grounded question + answer pair per chunk,
inserted into `synthetic_pairs` (chosen only, rejected/judge_* left null
until judge.py's audit pass runs).
"""

from __future__ import annotations

from agents.llm import AGENT_MODEL, call_claude
from synth_data.db import insert_pair, sample_chunks

SYSTEM_PROMPT = """You write training data for a grid-energy regulatory Q&A
assistant. Given one excerpt from a FERC order, ISO interconnection manual,
curtailment report, or industry report, write ONE realistic question a grid
analyst or developer might ask that this excerpt directly and fully answers,
plus the answer.

Rules:
- The answer must be fully supported by the excerpt alone -- no outside
  knowledge, no speculation.
- The question should be specific enough that this excerpt is a good answer
  to it (not so generic it could match any document).
- Write the answer as a standalone response (don't say "according to the
  excerpt" -- just answer as the assistant would to the end user)."""

QA_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "answer": {"type": "string"},
    },
    "required": ["question", "answer"],
}


def generate_qa_pair(chunk_text: str, doc_title: str) -> dict:
    """Returns {question, answer}."""
    user_prompt = f"Document: {doc_title}\n\nExcerpt:\n{chunk_text}"
    result = call_claude(
        system=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=AGENT_MODEL,
        max_tokens=600,
        tool_schema=QA_SCHEMA,
    )
    parsed = result.tool_input or {}
    return {
        "question": parsed.get("question", "").strip(),
        "answer": parsed.get("answer", "").strip(),
    }


def run(n_chunks: int = 40, seed: int | None = None) -> list[int]:
    """Samples n_chunks chunks (diverse across documents), generates one Q/A
    pair per chunk, inserts each into synthetic_pairs. Returns the new row ids.
    """
    chunks = sample_chunks(n_chunks, seed=seed)
    print(f"Sampled {len(chunks)} chunks across {len({c['document_id'] for c in chunks})} documents.")

    new_ids = []
    for i, chunk in enumerate(chunks, start=1):
        qa = generate_qa_pair(chunk["text"], chunk["doc_title"])
        if not qa["question"] or not qa["answer"]:
            print(f"  [{i}/{len(chunks)}] chunk#{chunk['chunk_id']} -- SKIPPED (empty generation)")
            continue
        pair_id = insert_pair(
            source_chunk_id=chunk["chunk_id"],
            prompt=qa["question"],
            chosen=qa["answer"],
        )
        new_ids.append(pair_id)
        print(f"  [{i}/{len(chunks)}] chunk#{chunk['chunk_id']} ({chunk['doc_title'][:40]}...) -> pair#{pair_id}")

    print(f"Inserted {len(new_ids)} SFT pairs into synthetic_pairs.")
    return new_ids


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate grounded SFT Q/A pairs from sampled chunks.")
    parser.add_argument("--n", type=int, default=40, help="number of chunks to sample")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    run(n_chunks=args.n, seed=args.seed)
