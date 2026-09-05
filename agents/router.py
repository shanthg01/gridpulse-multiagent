"""Router agent — classifies a query into which downstream agent(s) to invoke.
Cheap/fast model (Haiku), structured output via forced tool-use.
"""

from __future__ import annotations

from agents.llm import ROUTER_MODEL, LLMResult, call_claude

SYSTEM_PROMPT = """You are a routing agent for a grid-energy analysis assistant.
Given a user question, decide which tool(s) are needed to answer it:

- needs_rag: true if the question requires regulatory/policy documents
  (FERC orders, ISO interconnection manuals/BPMs, curtailment reports,
  decarbonization/queue-trend reports).
- needs_timeseries: true if the question requires querying hourly EIA
  generation-mix data (trends, totals, comparisons over time, by fuel type
  or balancing authority).

Many questions need both (e.g. "compare X's interconnection rules to Y's,
and what was Z's generation trend last month" -> both true).

Also extract an ISO/BA code if the question clearly targets one
(PJM, CISO/CAISO, ERCO/ERCOT, MISO), else null."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_rag": {"type": "boolean"},
        "needs_timeseries": {"type": "boolean"},
        "iso": {"type": ["string", "null"], "description": "BA code, e.g. PJM, CISO, ERCO, MISO, or null"},
        "reasoning": {"type": "string"},
    },
    "required": ["needs_rag", "needs_timeseries", "reasoning"],
}


def classify_query(query: str) -> tuple[dict, LLMResult]:
    result = call_claude(
        system=SYSTEM_PROMPT,
        user_prompt=query,
        model=ROUTER_MODEL,
        max_tokens=300,
        tool_schema=CLASSIFY_SCHEMA,
    )
    decision = result.tool_input or {"needs_rag": True, "needs_timeseries": False, "iso": None, "reasoning": "fallback: tool_input missing"}
    return decision, result
