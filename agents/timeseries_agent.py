"""Timeseries agent — NL -> structured query params -> pandas over eia_series
-> chart spec (Vega-Lite) + narrated summary.

Deliberately does NOT let the LLM write arbitrary SQL: it only extracts
{ba_code, fuel_type, start_date, end_date, metric}, and this module runs a
fixed, parameterized query against that. Keeps the tool surface safe and
the chart spec well-formed regardless of what the model outputs.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import psycopg2
from dotenv import load_dotenv

from agents.llm import AGENT_MODEL, ROUTER_MODEL, LLMResult, call_claude

load_dotenv(override=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://gridpulse:gridpulse@localhost:5433/gridpulse"
)

KNOWN_BAS = ["PJM", "CISO", "ERCO", "MISO"]


def _data_asof() -> str:
    """Reference date for resolving relative phrases ("last July") -- the
    most recent timestamp actually in eia_series, NOT the system clock.
    EIA's live API lags real-world time and this dev box's clock has been
    observed skewed ahead of it, so date.today() can resolve "last July"
    into a period with no ingested data. Anchoring to the data itself keeps
    relative-date queries answerable regardless of either clock.
    """
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT max(ts) FROM eia_series")
            row = cur.fetchone()
            if row and row[0]:
                return row[0].date().isoformat()
    finally:
        conn.close()
    return date.today().isoformat()


def _extract_system_prompt() -> str:
    return f"""Extract structured parameters for an EIA grid
generation-mix query. Known balancing authorities in the database: {', '.join(KNOWN_BAS)}
(CAISO -> CISO, ERCOT -> ERCO). If the question names an ISO not in this
list, still return it uppercased -- the query will simply return no data.

metric must be one of: "trend" (time series), "total" (sum over range),
"average" (mean over range), "share" (fuel mix as % of total).

Dates: if the question says a relative period like "last July", resolve it
using {_data_asof()} as the reference "today" (this is the latest date with
ingested data, not necessarily the real calendar date)."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "ba_code": {"type": "string"},
        "fuel_type": {"type": ["string", "null"], "description": "e.g. Solar, Wind, Natural Gas, or null for all fuels"},
        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
        "end_date": {"type": "string", "description": "YYYY-MM-DD"},
        "metric": {"type": "string", "enum": ["trend", "total", "average", "share"]},
    },
    "required": ["ba_code", "start_date", "end_date", "metric"],
}


def _extract_params(query: str) -> tuple[dict, LLMResult]:
    result = call_claude(
        system=_extract_system_prompt(),
        user_prompt=query,
        model=ROUTER_MODEL,
        max_tokens=300,
        tool_schema=EXTRACT_SCHEMA,
    )
    return result.tool_input or {}, result


def _query_series(params: dict) -> pd.DataFrame:
    # Manual fetch instead of pd.read_sql(conn=psycopg2 connection) -- pandas
    # only officially supports SQLAlchemy/sqlite3 connections and warns on
    # a raw DBAPI2 connection like this one.
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ts, fuel_type, mwh
                FROM eia_series
                WHERE ba_code = %(ba_code)s
                  AND ts >= %(start_date)s
                  AND ts < (%(end_date)s::date + interval '1 day')
                  AND (%(fuel_type)s IS NULL OR fuel_type = %(fuel_type)s)
                ORDER BY ts
                """,
                {
                    "ba_code": params.get("ba_code"),
                    "start_date": params.get("start_date"),
                    "end_date": params.get("end_date"),
                    "fuel_type": params.get("fuel_type"),
                },
            )
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=["ts", "fuel_type", "mwh"])
    finally:
        conn.close()


def _summarize(df: pd.DataFrame, metric: str) -> dict:
    if df.empty:
        return {"stats": {}, "chart_spec": None}

    if metric == "trend":
        series = df.groupby("ts")["mwh"].sum().reset_index()
        chart_spec = {
            "mark": "line",
            "encoding": {
                "x": {"field": "ts", "type": "temporal"},
                "y": {"field": "mwh", "type": "quantitative"},
            },
            "data": {"values": series.to_dict(orient="records")},
        }
        stats = {
            "min": float(series["mwh"].min()),
            "max": float(series["mwh"].max()),
            "mean": float(series["mwh"].mean()),
            "n_points": len(series),
        }
    elif metric == "share":
        by_fuel = df.groupby("fuel_type")["mwh"].sum()
        total = by_fuel.sum()
        share = (by_fuel / total * 100).round(2)
        chart_spec = {
            "mark": "arc",
            "encoding": {
                "theta": {"field": "mwh", "type": "quantitative"},
                "color": {"field": "fuel_type", "type": "nominal"},
            },
            "data": {"values": by_fuel.reset_index().to_dict(orient="records")},
        }
        stats = {"share_pct": share.to_dict()}
    elif metric == "total":
        stats = {"total_mwh": float(df["mwh"].sum())}
        chart_spec = None
    else:  # average
        stats = {"average_mwh": float(df["mwh"].mean())}
        chart_spec = None

    return {"stats": stats, "chart_spec": chart_spec}


def run_timeseries(query: str) -> dict:
    params, extract_result = _extract_params(query)
    if not params.get("ba_code"):
        return {
            "answer": "Could not determine which balancing authority/ISO this question refers to.",
            "params": params,
            "chart_spec": None,
            "llm_result": extract_result,
        }

    df = _query_series(params)
    summary = _summarize(df, params.get("metric", "trend"))

    if df.empty:
        answer = (
            f"No EIA generation data found for {params.get('ba_code')} "
            f"between {params.get('start_date')} and {params.get('end_date')}."
        )
        narrate_result = None
    else:
        narrate_prompt = (
            f"Question: {query}\n\n"
            f"Computed stats from EIA hourly generation data: {summary['stats']}\n"
            f"Query params used: {params}\n\n"
            "Write a concise (2-4 sentence) answer using these numbers. "
            "Don't invent data not present in the stats."
        )
        narrate_result = call_claude(
            system="You narrate grid generation-mix statistics concisely and precisely.",
            user_prompt=narrate_prompt,
            model=AGENT_MODEL,
            max_tokens=400,
        )
        answer = narrate_result.text

    return {
        "answer": answer,
        "params": params,
        "stats": summary["stats"],
        "chart_spec": summary["chart_spec"],
        "llm_result": narrate_result or extract_result,
    }
