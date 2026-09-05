"""LangGraph wiring: router -> rag_agent -> timeseries_agent -> synthesis_agent.

Both branch nodes run unconditionally but no-op internally when the router
didn't flag them as needed -- keeps the graph a simple linear chain instead
of conditional edges, cheap enough at this scale. (Fan them out in parallel
via LangGraph's conditional-edge branching if this ever becomes a bottleneck.)

Every node logs one row to `agent_steps`, keyed by `agent_runs.run_id` --
this is the entire trajectory tracer data source, no separate tracing infra.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, TypedDict

import psycopg2
from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

from agents.llm import log_step
from agents.rag_agent import run_rag
from agents.router import classify_query
from agents.synthesis_agent import synthesize
from agents.timeseries_agent import run_timeseries

load_dotenv(override=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://gridpulse:gridpulse@localhost:5433/gridpulse"
)


class GraphState(TypedDict, total=False):
    query: str
    iso: str | None
    needs_rag: bool
    needs_timeseries: bool
    rag_result: dict | None
    ts_result: dict | None
    final: dict | None
    run_id: str
    step_no: int
    conn: Any  # psycopg2 connection, carried through state for step logging


def _node_router(state: GraphState) -> dict:
    decision, result = classify_query(state["query"])
    step_no = state["step_no"] + 1
    log_step(
        state["conn"], state["run_id"], step_no, "router", "classify_query",
        state["query"], decision,
        result.tokens_in, result.tokens_out, result.latency_ms,
    )
    return {
        "needs_rag": decision.get("needs_rag", True),
        "needs_timeseries": decision.get("needs_timeseries", False),
        "iso": decision.get("iso"),
        "step_no": step_no,
    }


def _node_rag(state: GraphState) -> dict:
    if not state.get("needs_rag"):
        return {}
    result = run_rag(state["query"], iso=state.get("iso"))
    step_no = state["step_no"] + 1
    llm = result.get("llm_result")
    log_step(
        state["conn"], state["run_id"], step_no, "rag_agent", "hybrid_search",
        {"query": state["query"], "iso": state.get("iso")},
        {"answer": result["answer"], "n_citations": len(result["citations"])},
        llm.tokens_in if llm else 0, llm.tokens_out if llm else 0, llm.latency_ms if llm else 0,
        retrieval_score=result.get("retrieval_score"),
    )
    return {"rag_result": result, "step_no": step_no}


def _node_timeseries(state: GraphState) -> dict:
    if not state.get("needs_timeseries"):
        return {}
    result = run_timeseries(state["query"])
    step_no = state["step_no"] + 1
    llm = result.get("llm_result")
    log_step(
        state["conn"], state["run_id"], step_no, "timeseries_agent", "eia_series_query",
        {"query": state["query"]},
        {"answer": result["answer"], "params": result.get("params")},
        llm.tokens_in if llm else 0, llm.tokens_out if llm else 0, llm.latency_ms if llm else 0,
    )
    return {"ts_result": result, "step_no": step_no}


def _node_synthesis(state: GraphState) -> dict:
    final = synthesize(state["query"], state.get("rag_result"), state.get("ts_result"))
    step_no = state["step_no"] + 1
    llm = final.get("synthesis_llm_result")
    log_step(
        state["conn"], state["run_id"], step_no, "synthesis_agent", None,
        {"had_rag": bool(state.get("rag_result")), "had_timeseries": bool(state.get("ts_result"))},
        {"answer": final["answer"]},
        llm.tokens_in if llm else 0, llm.tokens_out if llm else 0, llm.latency_ms if llm else 0,
    )
    return {"final": final, "step_no": step_no}


def _build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("router", _node_router)
    graph.add_node("rag", _node_rag)
    graph.add_node("timeseries", _node_timeseries)
    graph.add_node("synthesis", _node_synthesis)

    graph.set_entry_point("router")
    graph.add_edge("router", "rag")
    graph.add_edge("rag", "timeseries")
    graph.add_edge("timeseries", "synthesis")
    graph.add_edge("synthesis", END)
    return graph.compile()


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


def create_run(query: str) -> str:
    """Inserts the agent_runs row and returns run_id, without executing the
    graph. Lets a caller (e.g. the API) hand back run_id immediately and run
    execute_run() in the background, so a client can poll progress via
    agent_steps while the graph is still working.
    """
    run_id = str(uuid.uuid4())
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_runs (run_id, query) VALUES (%s, %s)",
                (run_id, query),
            )
        conn.commit()
    finally:
        conn.close()
    return run_id


def execute_run(run_id: str, query: str) -> dict:
    """Runs the graph for an already-created run_id, persists the full
    result to agent_runs.result_json (this is what flips a poller's "done"
    check), and returns {run_id, answer, citations, chart_spec}.
    """
    conn = psycopg2.connect(DATABASE_URL)
    try:
        initial_state: GraphState = {
            "query": query,
            "run_id": run_id,
            "step_no": 0,
            "conn": conn,
        }
        final_state = _get_graph().invoke(initial_state)
        final = final_state.get("final") or {}

        result = {
            "run_id": run_id,
            "answer": final.get("answer", ""),
            "citations": final.get("citations", []),
            "chart_spec": final.get("chart_spec"),
        }

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_runs SET final_answer = %s, result_json = %s WHERE run_id = %s",
                (result["answer"], json.dumps(result, default=str), run_id),
            )
        conn.commit()

        return result
    finally:
        conn.close()


def run_query(query: str) -> dict:
    """Synchronous convenience wrapper (CLI, eval harness) -- creates and
    executes a run in one call. For a UI that wants live progress, use
    create_run() + execute_run() instead (see api/main.py's /query, /status).
    """
    run_id = create_run(query)
    return execute_run(run_id, query)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run a query through the full agent graph.")
    parser.add_argument("query")
    args = parser.parse_args()

    out = run_query(args.query)
    print(json.dumps(out, indent=2, default=str))
