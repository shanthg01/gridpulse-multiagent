"""Thin Claude call wrapper — timing + token accounting baked in, since every
call feeds the trajectory tracer (`agent_steps` table).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import anthropic
import psycopg2
from dotenv import load_dotenv

load_dotenv(override=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://gridpulse:gridpulse@localhost:5433/gridpulse"
)

# Cost/latency tiering per PLAN.md: Haiku for router/bulk work, Sonnet for
# final synthesis and anything answering the user directly.
ROUTER_MODEL = "claude-haiku-4-5-20251001"
AGENT_MODEL = "claude-sonnet-5"

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set (see .env.example)")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


@dataclass
class LLMResult:
    text: str
    tool_input: dict | None
    tokens_in: int
    tokens_out: int
    latency_ms: int


def call_claude(
    system: str,
    user_prompt: str,
    model: str = AGENT_MODEL,
    max_tokens: int = 1024,
    tool_schema: dict | None = None,
) -> LLMResult:
    """Single-turn call. If `tool_schema` given (a JSON-schema `input_schema`
    dict), forces the model to respond via that one tool and returns its
    parsed input as `tool_input`; otherwise `text` carries the plain reply.
    """
    client = _get_client()
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if tool_schema is not None:
        kwargs["tools"] = [
            {
                "name": "respond",
                "description": "Structured response",
                "input_schema": tool_schema,
            }
        ]
        kwargs["tool_choice"] = {"type": "tool", "name": "respond"}

    start = time.monotonic()
    resp = client.messages.create(**kwargs)
    latency_ms = int((time.monotonic() - start) * 1000)

    text = ""
    tool_input = None
    for block in resp.content:
        if block.type == "text":
            text += block.text
        elif block.type == "tool_use":
            tool_input = block.input

    return LLMResult(
        text=text,
        tool_input=tool_input,
        tokens_in=resp.usage.input_tokens,
        tokens_out=resp.usage.output_tokens,
        latency_ms=latency_ms,
    )


def log_step(
    run_id: str,
    step_no: int,
    agent_name: str,
    tool_called: str | None,
    input_: Any,
    output: Any,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
    retrieval_score: float | None = None,
) -> None:
    # Opens its own connection (matching every other module's pattern) rather
    # than taking a shared one -- log_step is called from graph nodes that
    # may run concurrently (parallel branches), and psycopg2 connections
    # aren't safe to share across threads.
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_steps
                    (run_id, step_no, agent_name, tool_called, input, output,
                     tokens_in, tokens_out, latency_ms, retrieval_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id, step_no, agent_name, tool_called,
                    json.dumps(input_, default=str), json.dumps(output, default=str),
                    tokens_in, tokens_out, latency_ms, retrieval_score,
                ),
            )
        conn.commit()
    finally:
        conn.close()
