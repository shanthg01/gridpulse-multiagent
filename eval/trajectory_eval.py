"""Trajectory eval harness — runs the golden Q/A set through the full agent
pipeline (agents.graph.run_query) and scores each run against the expected
tool path / citations / timeseries parameters recorded in
eval/fixtures/golden_qa.json.

Scoring per item:
  (a) tool-selection accuracy  -- router's actual needs_rag/needs_timeseries
      (read back from the `agent_steps` row for the router step) vs expected.
  (b) citation correctness     -- for RAG items, does the returned citation
      set's document_ids overlap expected_document_ids at all.
  (c) retrieval precision@k    -- direct rag.hybrid_search() call (bypassing
      the LLM) scored against expected_document_ids, for RAG items only.
  (d) timeseries correctness   -- for timeseries items, does the extracted
      ba_code match, and does an independent SQL computation over the
      *agent's own extracted params* land within the golden item's
      expected_value_range (or, for "share" items, does the top fuel/share
      match expected_top_fuel / expected_top_fuel_share_range).
  (e) latency and token cost   -- summed across every `agent_steps` row for
      that run_id.

Aggregates into a summary and:
  - prints a human-readable report
  - writes the full report to eval/results/latest.json

Usage:
    .venv/Scripts/python.exe -m eval.trajectory_eval
    .venv/Scripts/python.exe -m eval.trajectory_eval --limit 3   # smoke test
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(override=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://gridpulse:gridpulse@localhost:5433/gridpulse"
)

EVAL_DIR = Path(__file__).resolve().parent
FIXTURES_PATH = EVAL_DIR / "fixtures" / "golden_qa.json"
RESULTS_DIR = EVAL_DIR / "results"
RESULTS_PATH = RESULTS_DIR / "latest.json"

RETRIEVAL_TOP_K = 6  # matches rag_agent.run_rag's default top_k


def _load_golden_set() -> list[dict]:
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    return data["items"]


def _fetch_agent_steps(conn, run_id: str) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT step_no, agent_name, tool_called, input, output,
                   tokens_in, tokens_out, latency_ms, retrieval_score
            FROM agent_steps
            WHERE run_id = %s
            ORDER BY step_no
            """,
            (run_id,),
        )
        return list(cur.fetchall())


def _step_output(steps: list[dict], agent_name: str) -> dict | None:
    for s in steps:
        if s["agent_name"] == agent_name:
            out = s["output"]
            return json.loads(out) if isinstance(out, str) else out
    return None


def _totals(steps: list[dict]) -> dict:
    return {
        "tokens_in": sum(s["tokens_in"] or 0 for s in steps),
        "tokens_out": sum(s["tokens_out"] or 0 for s in steps),
        "latency_ms": sum(s["latency_ms"] or 0 for s in steps),
    }


def _retrieval_precision_at_k(question: str, iso: str | None, expected_doc_ids: list[int], k: int) -> float | None:
    # Imported lazily -- this module (and its embedding-model load) is only
    # needed when we actually score a RAG item, and importing it up front
    # would slow down --limit-based smoke runs and unit-test-only usage.
    from rag.hybrid_search import hybrid_search

    hits = hybrid_search(question, top_k=k, iso=iso)
    if not hits:
        return 0.0
    expected = set(expected_doc_ids)
    n_relevant = sum(1 for h in hits if h["document_id"] in expected)
    return n_relevant / len(hits)


def _query_series_rows(conn, ba_code: str | None, fuel_type: str | None, start_date: str | None, end_date: str | None) -> list[dict]:
    if not ba_code or not start_date or not end_date:
        return []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT fuel_type, mwh
            FROM eia_series
            WHERE ba_code = %(ba_code)s
              AND ts >= %(start_date)s
              AND ts < (%(end_date)s::date + interval '1 day')
              AND (%(fuel_type)s IS NULL OR fuel_type = %(fuel_type)s)
            """,
            {
                "ba_code": ba_code,
                "start_date": start_date,
                "end_date": end_date,
                "fuel_type": fuel_type,
            },
        )
        return list(cur.fetchall())


def _score_timeseries(conn, item: dict, ts_output: dict | None) -> dict:
    """Independently recomputes a stat from eia_series using the params the
    *agent itself* extracted (not the golden item's params) and checks it
    against the golden item's expected range. This validates that the
    extraction step produced parameters that land on real, sane data --
    not just that the agent echoed back the golden ba_code.
    """
    result: dict[str, Any] = {
        "actual_ba_code": None,
        "actual_fuel_type": None,
        "actual_metric": None,
        "ba_match": None,
        "value_in_range": None,
        "actual_stat_value": None,
        "share_check": None,
    }
    if ts_output is None:
        return result

    params = ts_output.get("params") or {}
    result["actual_ba_code"] = params.get("ba_code")
    result["actual_fuel_type"] = params.get("fuel_type")
    result["actual_metric"] = params.get("metric")

    expected_ba = item.get("expected_ba_code")
    if expected_ba:
        result["ba_match"] = (params.get("ba_code") or "").upper() == expected_ba.upper()

    rows = _query_series_rows(
        conn, params.get("ba_code"), params.get("fuel_type"),
        params.get("start_date"), params.get("end_date"),
    )
    if not rows:
        return result

    if item.get("expected_metric") == "share":
        totals: dict[str, float] = {}
        for r in rows:
            totals[r["fuel_type"]] = totals.get(r["fuel_type"], 0.0) + (r["mwh"] or 0.0)
        grand_total = sum(totals.values())
        if grand_total:
            top_fuel = max(totals, key=totals.get)
            top_share = totals[top_fuel] / grand_total * 100
            result["actual_stat_value"] = {"top_fuel": top_fuel, "top_share_pct": round(top_share, 1)}
            expected_top = item.get("expected_top_fuel")
            lo, hi = item.get("expected_top_fuel_share_range", [None, None])
            share_ok = top_fuel == expected_top and lo is not None and lo <= top_share <= hi
            result["share_check"] = share_ok
            result["value_in_range"] = share_ok
        return result

    values = [r["mwh"] for r in rows if r["mwh"] is not None]
    if not values:
        return result

    metric = item.get("expected_metric", params.get("metric"))
    if metric == "total":
        stat = sum(values)
    elif metric == "average":
        stat = sum(values) / len(values)
    else:  # "trend" or unknown -- range in golden set is defined as per-hour bounds
        stat = sum(values) / len(values)
    result["actual_stat_value"] = stat

    rng = item.get("expected_value_range")
    if rng:
        lo, hi = rng
        result["value_in_range"] = lo <= stat <= hi
    return result


def _score_item(conn, item: dict, agent_result: dict) -> dict:
    run_id = agent_result["run_id"]
    steps = _fetch_agent_steps(conn, run_id)
    router_out = _step_output(steps, "router") or {}
    ts_out = _step_output(steps, "timeseries_agent")
    totals = _totals(steps)

    actual_needs_rag = bool(router_out.get("needs_rag"))
    actual_needs_timeseries = bool(router_out.get("needs_timeseries"))
    tool_selection_correct = (
        actual_needs_rag == bool(item["expected_needs_rag"])
        and actual_needs_timeseries == bool(item["expected_needs_timeseries"])
    )

    citation_correct = None
    precision_at_k = None
    actual_document_ids: list[int] = []
    if item["expected_needs_rag"]:
        citations = agent_result.get("citations") or []
        actual_document_ids = sorted({c.get("document_id") for c in citations if c.get("document_id") is not None})
        expected_doc_ids = item.get("expected_document_ids") or []
        if expected_doc_ids:
            citation_correct = bool(set(actual_document_ids) & set(expected_doc_ids))
            precision_at_k = _retrieval_precision_at_k(
                item["question"], item.get("expected_iso"), expected_doc_ids, RETRIEVAL_TOP_K
            )

    ts_score = {}
    if item["expected_needs_timeseries"]:
        ts_score = _score_timeseries(conn, item, ts_out)

    # Overall pass: tool selection must be right, plus whichever of
    # citation/timeseries checks apply must also pass (None = not applicable
    # = doesn't block the pass).
    checks = [tool_selection_correct]
    if citation_correct is not None:
        checks.append(citation_correct)
    if item["expected_needs_timeseries"]:
        if ts_score.get("ba_match") is not None:
            checks.append(bool(ts_score["ba_match"]))
        if ts_score.get("value_in_range") is not None:
            checks.append(bool(ts_score["value_in_range"]))
    passed = all(checks)

    return {
        "id": item["id"],
        "question": item["question"],
        "run_id": run_id,
        "expected_needs_rag": item["expected_needs_rag"],
        "expected_needs_timeseries": item["expected_needs_timeseries"],
        "actual_needs_rag": actual_needs_rag,
        "actual_needs_timeseries": actual_needs_timeseries,
        "tool_selection_correct": tool_selection_correct,
        "expected_document_ids": item.get("expected_document_ids"),
        "actual_document_ids": actual_document_ids,
        "citation_correct": citation_correct,
        "retrieval_precision_at_k": precision_at_k,
        **{f"ts_{k}": v for k, v in ts_score.items()},
        "tokens_in": totals["tokens_in"],
        "tokens_out": totals["tokens_out"],
        "latency_ms": totals["latency_ms"],
        "answer_preview": (agent_result.get("answer") or "")[:300],
        "passed": passed,
    }


def _aggregate(results: list[dict]) -> dict:
    n = len(results)
    tool_sel_acc = sum(r["tool_selection_correct"] for r in results) / n if n else 0.0

    rag_items = [r for r in results if r["citation_correct"] is not None]
    citation_acc = (sum(r["citation_correct"] for r in rag_items) / len(rag_items)) if rag_items else None

    precisions = [r["retrieval_precision_at_k"] for r in results if r["retrieval_precision_at_k"] is not None]
    avg_precision_at_k = sum(precisions) / len(precisions) if precisions else None

    ts_items = [r for r in results if r["expected_needs_timeseries"]]
    ts_ba_matches = [r["ts_ba_match"] for r in ts_items if r.get("ts_ba_match") is not None]
    ts_ba_match_rate = sum(ts_ba_matches) / len(ts_ba_matches) if ts_ba_matches else None
    ts_value_checks = [r["ts_value_in_range"] for r in ts_items if r.get("ts_value_in_range") is not None]
    ts_value_in_range_rate = sum(ts_value_checks) / len(ts_value_checks) if ts_value_checks else None

    overall_accuracy = sum(r["passed"] for r in results) / n if n else 0.0

    total_tokens = sum(r["tokens_in"] + r["tokens_out"] for r in results)
    total_latency = sum(r["latency_ms"] for r in results)

    return {
        "n_items": n,
        "tool_selection_accuracy": round(tool_sel_acc, 4),
        "citation_correctness": round(citation_acc, 4) if citation_acc is not None else None,
        "retrieval_precision_at_k": round(avg_precision_at_k, 4) if avg_precision_at_k is not None else None,
        "timeseries_ba_match_rate": round(ts_ba_match_rate, 4) if ts_ba_match_rate is not None else None,
        "timeseries_value_in_range_rate": round(ts_value_in_range_rate, 4) if ts_value_in_range_rate is not None else None,
        "overall_accuracy": round(overall_accuracy, 4),
        "avg_tokens_per_query": round(total_tokens / n, 1) if n else 0.0,
        "avg_latency_ms_per_query": round(total_latency / n, 1) if n else 0.0,
        "total_tokens": total_tokens,
        "total_latency_ms": total_latency,
    }


def _print_report(results: list[dict], summary: dict) -> None:
    print("\n" + "=" * 78)
    print("TRAJECTORY EVAL -- per-item results")
    print("=" * 78)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"[{status}] {r['id']}")
        print(f"       needs_rag: expected={r['expected_needs_rag']!s:5} actual={r['actual_needs_rag']!s:5} | "
              f"needs_timeseries: expected={r['expected_needs_timeseries']!s:5} actual={r['actual_needs_timeseries']!s:5}")
        if r["citation_correct"] is not None:
            print(f"       citations: expected_docs={r['expected_document_ids']} actual_docs={r['actual_document_ids']} "
                  f"correct={r['citation_correct']} precision@k={r['retrieval_precision_at_k']}")
        if r.get("ts_actual_ba_code") is not None:
            print(f"       timeseries: ba={r['ts_actual_ba_code']} metric={r['ts_actual_metric']} "
                  f"stat={r['ts_actual_stat_value']} ba_match={r['ts_ba_match']} value_in_range={r['ts_value_in_range']}")
        print(f"       tokens_in+out={r['tokens_in']+r['tokens_out']} latency_ms={r['latency_ms']}")
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("=" * 78 + "\n")


def run_eval(limit: int | None = None) -> dict:
    from agents.graph import run_query  # deferred: avoid import cost for --help etc.

    items = _load_golden_set()
    if limit:
        items = items[:limit]

    conn = psycopg2.connect(DATABASE_URL)
    results = []
    try:
        for i, item in enumerate(items, start=1):
            print(f"[{i}/{len(items)}] running: {item['id']} ...", file=sys.stderr)
            t0 = time.monotonic()
            agent_result = run_query(item["question"])
            wall_s = time.monotonic() - t0
            print(f"    done in {wall_s:.1f}s (run_id={agent_result['run_id']})", file=sys.stderr)
            results.append(_score_item(conn, item, agent_result))
    finally:
        conn.close()

    summary = _aggregate(results)
    _print_report(results, summary)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {"summary": summary, "results": results}
    RESULTS_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote full report to {RESULTS_PATH}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the golden Q/A set through the full agent pipeline and score it.")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N golden items (smoke test).")
    args = parser.parse_args()
    run_eval(limit=args.limit)
