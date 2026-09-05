"""Synthesis agent — merges RAG + timeseries branch outputs into one answer.
Skips an LLM call entirely when only one branch ran (nothing to merge).
"""

from __future__ import annotations

from agents.llm import AGENT_MODEL, LLMResult, call_claude

SYSTEM_PROMPT = """You combine a policy/regulatory answer (with citations) and
a grid generation-data answer into one coherent response to the user's
original question. Preserve all citation markers ([C1], [C2], ...) exactly
as given -- do not renumber or drop them. Be concise."""


def synthesize(query: str, rag_result: dict | None, ts_result: dict | None) -> dict:
    if rag_result and not ts_result:
        return {**rag_result, "chart_spec": None, "synthesis_llm_result": None}
    if ts_result and not rag_result:
        return {**ts_result, "citations": [], "synthesis_llm_result": None}
    if not rag_result and not ts_result:
        return {
            "answer": "Unable to answer -- no retrieval or timeseries branch produced a result.",
            "citations": [],
            "chart_spec": None,
            "synthesis_llm_result": None,
        }

    user_prompt = (
        f"Original question: {query}\n\n"
        f"Policy/regulatory answer:\n{rag_result['answer']}\n\n"
        f"Grid data answer:\n{ts_result['answer']}"
    )
    result: LLMResult = call_claude(
        system=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=AGENT_MODEL,
        max_tokens=2048,
    )
    return {
        "answer": result.text,
        "citations": rag_result.get("citations", []),
        "chart_spec": ts_result.get("chart_spec"),
        "synthesis_llm_result": result,
    }
