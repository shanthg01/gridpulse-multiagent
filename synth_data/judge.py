"""LLM-as-judge: scores a candidate answer's factuality/groundedness against
its source chunk, and (for DPO) picks the better of two candidates.

Scale: judge_score is a float in [0.0, 1.0], where
  1.0 = fully grounded -- every claim in the answer is directly supported by
        the source chunk text, no fabrication/extrapolation
  0.5 = partially grounded -- mostly supported but includes some unsupported
        detail, hedging, or minor drift from the source
  0.0 = ungrounded/contradicted -- answer is unsupported by, or contradicts,
        the source chunk

Uses forced structured output (tool_schema) exactly like agents/router.py's
CLASSIFY_SCHEMA pattern, via the shared agents.llm.call_claude helper.

Chosen model: ROUTER_MODEL (Haiku) -- judging is a cheap, high-volume,
short-output task, matching PLAN.md's "Haiku for router/judge/bulk gen"
cost guardrail.
"""

from __future__ import annotations

from agents.llm import ROUTER_MODEL, call_claude

SCORE_SYSTEM_PROMPT = """You are a strict factuality/groundedness judge for a
grid-energy regulatory Q&A dataset. You are given a source excerpt and a
candidate answer to a question about that excerpt. Score how well the answer
is grounded in ONLY the source excerpt -- penalize any claim not supported by
the excerpt, even if the claim happens to be true in the real world.

Score on a 0.0-1.0 scale:
  1.0 = fully grounded, every claim directly supported by the excerpt
  0.5 = partially grounded, mostly supported but some unsupported/extra detail
  0.0 = ungrounded or contradicts the excerpt

Give a short (1-2 sentence) rationale for the score."""

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "description": "Groundedness score, 0.0 to 1.0"},
        "rationale": {"type": "string", "description": "1-2 sentence justification"},
    },
    "required": ["score", "rationale"],
}

COMPARE_SYSTEM_PROMPT = """You are a strict factuality/groundedness judge for a
grid-energy regulatory Q&A dataset. You are given a source excerpt, a
question, and two candidate answers ("A" and "B"). Pick which answer is
better grounded in ONLY the source excerpt (more accurate, more complete,
less fabrication/extrapolation beyond the excerpt). Ties should be broken by
completeness and clarity.

Give:
  - winner: "a" or "b"
  - score: your confidence the winner is well-grounded, 0.0 to 1.0 (this is
    NOT a margin between the two, it is an absolute groundedness score for
    the winning answer)
  - rationale: 1-2 sentences explaining the choice"""

COMPARE_SCHEMA = {
    "type": "object",
    "properties": {
        "winner": {"type": "string", "enum": ["a", "b"]},
        "score": {"type": "number", "description": "Groundedness score of the winning answer, 0.0 to 1.0"},
        "rationale": {"type": "string", "description": "1-2 sentence justification"},
    },
    "required": ["winner", "score", "rationale"],
}


def score_answer(chunk_text: str, question: str, answer: str) -> dict:
    """Single-answer groundedness audit. Returns {score, rationale}."""
    user_prompt = (
        f"Source excerpt:\n{chunk_text}\n\n"
        f"Question: {question}\n\n"
        f"Candidate answer:\n{answer}"
    )
    result = call_claude(
        system=SCORE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=ROUTER_MODEL,
        max_tokens=300,
        tool_schema=SCORE_SCHEMA,
    )
    parsed = result.tool_input or {"score": 0.0, "rationale": "judge call failed to return structured output"}
    return {"score": float(parsed.get("score", 0.0)), "rationale": parsed.get("rationale", "")}


def compare_answers(chunk_text: str, question: str, answer_a: str, answer_b: str) -> dict:
    """Pairwise DPO judge. Returns {winner: 'a'|'b', score, rationale}."""
    user_prompt = (
        f"Source excerpt:\n{chunk_text}\n\n"
        f"Question: {question}\n\n"
        f"Answer A:\n{answer_a}\n\n"
        f"Answer B:\n{answer_b}"
    )
    result = call_claude(
        system=COMPARE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=ROUTER_MODEL,
        max_tokens=300,
        tool_schema=COMPARE_SCHEMA,
    )
    parsed = result.tool_input or {"winner": "a", "score": 0.0, "rationale": "judge call failed to return structured output"}
    winner = parsed.get("winner", "a")
    if winner not in ("a", "b"):
        winner = "a"
    return {"winner": winner, "score": float(parsed.get("score", 0.0)), "rationale": parsed.get("rationale", "")}


def audit_pairs(limit: int | None = None, flag_threshold: float = 0.5) -> dict:
    """Audit pass over existing SFT-style rows (rejected IS NULL, judge_score
    IS NULL yet) in `synthetic_pairs`: scores each `chosen` answer against its
    source chunk and writes judge_score/judge_rationale back. Low scorers
    (< flag_threshold) are flagged (score persisted, visible to export.py's
    threshold filter) rather than deleted -- keeps the raw generation
    reviewable.
    """
    from synth_data.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT sp.id, sp.prompt, sp.chosen, c.text
                FROM synthetic_pairs sp
                JOIN chunks c ON c.id = sp.source_chunk_id
                WHERE sp.rejected IS NULL AND sp.judge_score IS NULL
                ORDER BY sp.id
            """
            if limit is not None:
                query += " LIMIT %s"
                cur.execute(query, (limit,))
            else:
                cur.execute(query)
            rows = cur.fetchall()

        scored = 0
        flagged = 0
        for pair_id, prompt, chosen, chunk_text in rows:
            result = score_answer(chunk_text, prompt, chosen)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE synthetic_pairs SET judge_score = %s, judge_rationale = %s WHERE id = %s",
                    (result["score"], result["rationale"], pair_id),
                )
            conn.commit()
            scored += 1
            flag = result["score"] < flag_threshold
            if flag:
                flagged += 1
            print(
                f"  [{'FLAG' if flag else 'ok'}] pair#{pair_id} score={result['score']:.2f} "
                f"-- {result['rationale'][:100]}"
            )
        return {"scored": scored, "flagged": flagged}
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LLM-as-judge audit pass over synthetic_pairs.")
    parser.add_argument("--limit", type=int, default=None, help="max unaudited SFT rows to score")
    parser.add_argument("--flag-threshold", type=float, default=0.5)
    args = parser.parse_args()

    print(f"Auditing SFT rows (flag threshold={args.flag_threshold})...")
    summary = audit_pairs(limit=args.limit, flag_threshold=args.flag_threshold)
    print(f"Done. scored={summary['scored']} flagged={summary['flagged']}")
