# Backlog

Brainstormed 2026-09-05. Not yet scoped/prioritized into PLAN.md phases — pick items as they become relevant.

## Performance

Root cause of "document search is slow": retrieval itself (pgvector HNSW + tsvector) is sub-second at 2398 chunks. The latency is the Sonnet synthesis call after retrieval (~7K input tokens, 10-12s). Ranked by impact/effort:

1. **Parallelize rag_agent + timeseries_agent.** Independent branches, currently run strictly sequential in `agents/graph.py` (already flagged as a TODO comment there). LangGraph supports fan-out/fan-in — router → {rag, timeseries} concurrently → synthesis. Cuts hybrid-query wall time ~30-40%, low effort (graph topology change only).
2. **Stream the final answer** (SSE / Anthropic streaming API) instead of waiting for full completion. Pairs with the progress-stepper UI already built — replace "Synthesizing answer" spinner with live tokens. Kills perceived latency even though real latency is unchanged.
3. **Skip the second LLM call in timeseries_agent for trivial metrics.** "total"/"average" queries do extract-params (Haiku) → narrate-stats (Sonnet); the narration is templatable without an LLM for simple cases. Only "trend"/"share" need real narrative synthesis.
4. **Connection pooling.** Every function opens a fresh `psycopg2.connect()` (router/rag/timeseries/synthesis/log_step each pay setup cost). Swap for a shared pool (`psycopg2.pool` or SQLAlchemy engine).
5. **Trim RAG context.** top_k=6 chunks × ~500-650 words each is most of the input token cost. Try top_k=4, or cap oversized chunks (FERC Order 2023's blocks run long).
6. **Preload the embedding model at API startup**, not lazy-first-call (`rag/embed.py`'s `lru_cache` currently eats cold-start cost on the first real request in a session).
7. **Semantic response cache** for near-duplicate queries (exact or embedding-similarity match) — skip the whole graph, return a cached answer. Good for demo/repeat-query traffic, doesn't help genuinely novel questions.

Priority pick: (1) and (2) — biggest perceived+real impact, moderate effort.

## Features (domain-specific, grid/policy)

Grouped by what makes this domain interesting rather than a generic RAG-chatbot feature list:

**Docs are regulatory and versioned:**
- **Redline/version diffing** — the corpus already includes "redline" BPM PDFs (CAISO GIDAP, Generator Management). Diff two versions of the same ISO manual and summarize what changed (new deposit amount, new deadline).
- **Cross-ISO comparison matrix** — auto-build a structured table (study timeline, deposit $, cluster cadence) across PJM/CAISO/MISO/ERCOT pulled via RAG, instead of prose. Matches the "Grid Transition Analyst" persona from the original pitch directly.

**Citation data (page/bbox) already captured:**
- **Inline PDF snippet popover** — hover a citation chip, see the actual highlighted PDF region inline. Upgrades today's MVP citation list toward the PDF.js stretch goal already deferred in Phase 6; `Citation.bbox` already flows through the frontend types.

**LLM-as-judge (`synth_data/judge.py`) already built for Phase 4:**
- **Live confidence/grounding meter on answers** — reuse the judge module at query time (not just synthetic-data audit time) to flag weakly-grounded answers instead of presenting them with false confidence.

**`agent_steps` already captures full cost/latency:**
- **Cost/usage dashboard** — aggregate tokens/latency/$ across all runs. Data's already there; just needs a view.

**Map/visual promise from the original pitch:**
- **Actual geographic map view** — choropleth of ISO/BA regions colored by live fuel mix or curtailment %, clickable into that region's chat. Today's "Interactive Grid & Citation Map" is really just a line/pie chart panel.

**Bigger lifts, higher payoff:**
- **Multi-turn conversation** — follow-up questions referencing prior answers (every query is currently stateless).
- **Report/memo export** — turn a Q&A session into a formatted, properly-footnoted one-pager.
- **Proactive alerts** — "notify me if CAISO curtailment exceeds X this week," scheduled check against `eia_series`.

Top pick for portfolio impact (genuinely domain-specific, reuse existing infra): **redline diffing + comparison matrix + confidence meter**.
