"""DPO pair generation: for a subset of sampled chunks, generates a question
(same technique as qa_generator), then TWO candidate answers to that question
from two different LLM configurations, has judge.compare_answers pick the
better one, and writes prompt+chosen+rejected+judge_score+judge_rationale
into `synthetic_pairs`.

Multi-LLM stand-in (see synth_data/__init__.py and PLAN.md Phase 4): this repo
has no OpenAI or local-Ollama key configured, so instead of literally calling
multiple providers, "candidate A" and "candidate B" are two distinct Claude
configurations:
  - Candidate A: AGENT_MODEL (Sonnet), a "thorough analyst" persona that is
    encouraged to elaborate on implications and cross-reference detail.
  - Candidate B: ROUTER_MODEL (Haiku), a "terse assistant" persona instructed
    to answer as briefly as possible.
This gives genuinely different answer *distributions* (different model
capability tier + different instruction-following persona), which is enough
to produce meaningfully different chosen/rejected pairs for DPO, but it is
NOT the same as sampling from independent model families -- documented here
rather than silently implied.
"""

from __future__ import annotations

from agents.llm import AGENT_MODEL, ROUTER_MODEL, call_claude
from synth_data.db import insert_pair, sample_chunks
from synth_data.judge import compare_answers
from synth_data.qa_generator import generate_qa_pair

CANDIDATE_A_SYSTEM = """You are a thorough grid-policy research analyst.
Answer the user's question using ONLY the source excerpt provided. Elaborate
on relevant detail, implications, and any conditions/exceptions mentioned in
the excerpt, but do not introduce any claim the excerpt doesn't support. Keep
the full answer under 300 words."""

CANDIDATE_B_SYSTEM = """You are a terse grid-policy assistant. Answer the
user's question using ONLY the source excerpt provided, in as few sentences
as possible while still being correct. Do not introduce any claim the
excerpt doesn't support."""


def generate_candidate(system_prompt: str, model: str, chunk_text: str, question: str, max_tokens: int = 900) -> str:
    user_prompt = f"Source excerpt:\n{chunk_text}\n\nQuestion: {question}"
    result = call_claude(
        system=system_prompt,
        user_prompt=user_prompt,
        model=model,
        max_tokens=max_tokens,
    )
    return result.text.strip()


def run(n_chunks: int = 10, seed: int | None = None) -> list[int]:
    """Samples n_chunks chunks, generates one question + two candidate
    answers per chunk, judges the pair, inserts a DPO row per chunk. Returns
    the new row ids.
    """
    chunks = sample_chunks(n_chunks, seed=seed)
    print(f"Sampled {len(chunks)} chunks across {len({c['document_id'] for c in chunks})} documents for DPO generation.")

    new_ids = []
    for i, chunk in enumerate(chunks, start=1):
        qa = generate_qa_pair(chunk["text"], chunk["doc_title"])
        question = qa["question"]
        if not question:
            print(f"  [{i}/{len(chunks)}] chunk#{chunk['chunk_id']} -- SKIPPED (empty question)")
            continue

        answer_a = generate_candidate(CANDIDATE_A_SYSTEM, AGENT_MODEL, chunk["text"], question)
        answer_b = generate_candidate(CANDIDATE_B_SYSTEM, ROUTER_MODEL, chunk["text"], question)

        if not answer_a or not answer_b:
            print(f"  [{i}/{len(chunks)}] chunk#{chunk['chunk_id']} -- SKIPPED (empty candidate)")
            continue

        verdict = compare_answers(chunk["text"], question, answer_a, answer_b)
        chosen, rejected = (answer_a, answer_b) if verdict["winner"] == "a" else (answer_b, answer_a)

        pair_id = insert_pair(
            source_chunk_id=chunk["chunk_id"],
            prompt=question,
            chosen=chosen,
            rejected=rejected,
            judge_score=verdict["score"],
            judge_rationale=verdict["rationale"],
        )
        new_ids.append(pair_id)
        print(
            f"  [{i}/{len(chunks)}] chunk#{chunk['chunk_id']} ({chunk['doc_title'][:40]}...) "
            f"-> pair#{pair_id} winner={verdict['winner']} score={verdict['score']:.2f}"
        )

    print(f"Inserted {len(new_ids)} DPO pairs into synthetic_pairs.")
    return new_ids


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate DPO chosen/rejected pairs from sampled chunks.")
    parser.add_argument("--n", type=int, default=10, help="number of chunks to sample")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    run(n_chunks=args.n, seed=args.seed)
