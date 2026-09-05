# GridPulse AI — Implementation Plan

Multi-agent RAG system + synthetic data engine for regulatory policy and time-series grid energy analysis.

## Stack

- **Orchestration**: LangGraph (router + subagents, cyclic graphs, tool-use agents)
- **Vector/sparse store**: Postgres + `pgvector` (dense) + `pg_trgm`/`tsvector` (sparse) — one DB, hybrid query via RRF fusion. Dockerized.
- **Embeddings**: `text-embedding-3-large` (OpenAI) or local `bge-large-en-v1.5` via `sentence-transformers` (zero API cost option)
- **LLM**: Claude Sonnet 5 for agent reasoning + judge; Haiku for router + bulk synthetic gen (cost control)
- **Time-series store**: same Postgres, separate schema, raw EIA pulls cached
- **API layer**: FastAPI (`/query`, `/trace/{run_id}`, `/citations/{doc_id}`)
- **Frontend**: Next.js + Recharts (grid mix charts) + PDF.js viewer (citation highlight)
- **Eval harness**: custom pytest-based agent trajectory eval, run in GH Actions CI

## Repo structure

```
gridpulse-multiagent/
├── docker-compose.yml          # postgres+pgvector, adminer
├── infra/
│   └── init.sql                # extensions, schemas, tables
├── ingestion/
│   ├── eia_client.py           # EIA API v2 pull, hourly gen mix per BA/ISO
│   ├── ferc_scraper.py         # FERC eLibrary filings download
│   ├── iso_manuals.py          # PJM/CAISO/MISO manual PDF fetch
│   └── loaders/                # PDF→chunks (unstructured/pymupdf)
├── rag/
│   ├── chunking.py             # section-aware split for regulatory PDFs
│   ├── embed.py
│   ├── hybrid_search.py        # dense + sparse + RRF fusion
│   └── citation.py             # retrieved chunk → page/bbox → PDF snippet highlight
├── agents/
│   ├── router.py               # needs_rag / needs_timeseries / both
│   ├── rag_agent.py
│   ├── timeseries_agent.py     # NL → pandas/SQL over eia_series, chart spec out
│   ├── synthesis_agent.py      # merge branches, resolve conflicts, attach citations
│   └── graph.py                # LangGraph wiring
├── synth_data/
│   ├── qa_generator.py         # multi-LLM QA pair gen from chunks
│   ├── dpo_pairs.py            # chosen/rejected preference pairs
│   └── judge.py                # LLM-as-judge scoring/audit
├── eval/
│   ├── trajectory_eval.py      # tool-call correctness, retrieval precision@k
│   ├── fixtures/               # golden Q/A set
│   └── ci_gate.py              # fail build on regression vs baseline
├── api/
│   └── main.py
├── frontend/                   # Next.js app
└── tests/
```

## Data model (Postgres)

- `documents` (id, source_type[ferc|iso_manual|decarb_report], iso, url, fetched_at)
- `chunks` (id, document_id, page, bbox, text, embedding vector(1536), tsv tsvector)
- `eia_series` (ba_code, fuel_type, ts, mwh, region)
- `agent_runs` (run_id, query, started_at, final_answer)
- `agent_steps` (run_id, step_no, agent_name, tool_called, input, output, tokens_in, tokens_out, latency_ms, retrieval_score)
- `synthetic_pairs` (id, source_chunk_id, prompt, chosen, rejected, judge_score, judge_rationale)

Trajectory tracer UI reads directly off `agent_steps` — no separate tracing infra needed.

## Phases

**Phase 0 — Infra**
- `docker-compose.yml`: postgres:16 + pgvector, adminer
- `.env`: `EIA_API_KEY` (free, eia.gov/opendata), `ANTHROPIC_API_KEY`
- `init.sql`: extensions + schema

**Phase 1 — Ingestion**
- EIA client: hourly gen mix by BA (`/v2/electricity/rto/fuel-type-data`), backfill 1-2yr, few ISOs (PJM, CAISO, ERCOT, MISO)
- FERC: no clean bulk API — hand-curate ~30-50 key filings/orders (Order 2023 interconnection, ISO tariffs) as PDFs
- ISO manuals: PJM Manual 14, CAISO BPM docs — direct PDF download
- Chunk: section/heading-aware split, ~500-800 tok, store page+bbox (pymupdf) for citation

**Phase 2 — Hybrid RAG**
- Dense: pgvector cosine `<=>`, ivfflat/hnsw index
- Sparse: `tsvector` + GIN index, `ts_rank_cd`
- Fusion: RRF combine rankings, top-k → LLM context
- Citation: hit → doc_id/page/bbox → PDF.js viewer highlight

**Phase 3 — Multi-agent orchestration (LangGraph)**
- Router: classify query → needs_rag / needs_timeseries / both (structured output, Haiku)
- RAG agent: hybrid search → synthesize w/ citations
- Timeseries agent: NL → pandas/SQL over `eia_series`, emit Vega-Lite chart spec
- Synthesis agent: combine branches, attach citations + chart spec
- Every step logged to `agent_steps` (tool, tokens, latency, retrieval confidence)

**Phase 4 — Synthetic data foundry**
- QA gen: sample chunks → prompt multiple LLMs (Claude + GPT + local Llama via Ollama) → QA pairs
- DPO pairs: multiple candidate answers per prompt, judge ranks → chosen/rejected
- LLM-as-judge: score factuality/groundedness vs source chunk, reject low scorers, store rationale
- Output: exportable JSONL (SFT + DPO format)

**Phase 5 — Eval harness (CI)**
- Golden set: ~30-50 curated Q/A w/ expected tool path + expected citation
- Metrics: tool-selection accuracy, retrieval precision@k, citation correctness, groundedness, latency/cost
- GH Actions: run on PR, gate merge vs baseline

**Phase 6 — Frontend**
- Grid & Citation Map: stacked area chart (Recharts) + doc viewer, clickable citation chips → scroll+highlight PDF
- Trajectory Tracer panel: timeline of agent_steps, expandable per-step
- Query box + streaming answer (SSE from FastAPI)

## Cost/scope guardrails

- EIA API free
- FERC/ISO corpus: hand-curated ~50-100 docs, not full crawl
- Haiku for router/judge/bulk gen, Sonnet only for final synthesis
- Local embeddings (bge) optional to zero embed cost
- Single-node Postgres docker sufficient — no Timescale/Pinecone/Weaviate needed at this scale

## Build order

1. docker-compose + schema
2. EIA ingestion + timeseries_agent (standalone demo: NL→chart)
3. Curate 20 docs, hybrid RAG (standalone demo: cited Q&A)
4. LangGraph router tying both together
5. Trajectory logging + minimal tracer UI
6. Synthetic data foundry (parallel, independent of 1-5)
7. Eval harness + CI gate last, once golden set stabilizes
