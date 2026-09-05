-- GridPulse AI schema init

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

-- ============ Documents / RAG corpus ============

CREATE TABLE IF NOT EXISTS documents (
    id            BIGSERIAL PRIMARY KEY,
    source_type   TEXT NOT NULL CHECK (source_type IN ('ferc', 'iso_manual', 'decarb_report')),
    iso           TEXT,
    title         TEXT,
    url           TEXT UNIQUE,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id            BIGSERIAL PRIMARY KEY,
    document_id   BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page          INT,
    bbox          JSONB,
    text          TEXT NOT NULL,
    embedding     vector(768),  -- BAAI/bge-base-en-v1.5 dim; change if swapping embed model
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS chunks_tsv_gin_idx
    ON chunks USING gin (tsv);

CREATE INDEX IF NOT EXISTS chunks_document_id_idx
    ON chunks (document_id);

-- ============ Time-series (EIA) ============

CREATE TABLE IF NOT EXISTS eia_series (
    id            BIGSERIAL PRIMARY KEY,
    ba_code       TEXT NOT NULL,
    fuel_type     TEXT NOT NULL,
    region        TEXT,
    ts            TIMESTAMPTZ NOT NULL,
    mwh           DOUBLE PRECISION,
    UNIQUE (ba_code, fuel_type, ts)
);

CREATE INDEX IF NOT EXISTS eia_series_ba_ts_idx
    ON eia_series (ba_code, ts);

-- ============ Agent runs / trajectory tracer ============

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query         TEXT NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    final_answer  TEXT,
    result_json   JSONB  -- full {answer, citations, chart_spec}; NULL while the run is still in flight
);

CREATE TABLE IF NOT EXISTS agent_steps (
    id               BIGSERIAL PRIMARY KEY,
    run_id           UUID NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    step_no          INT NOT NULL,
    agent_name       TEXT NOT NULL,
    tool_called      TEXT,
    input            JSONB,
    output           JSONB,
    tokens_in        INT,
    tokens_out       INT,
    latency_ms       INT,
    retrieval_score  DOUBLE PRECISION,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS agent_steps_run_id_idx
    ON agent_steps (run_id, step_no);

-- ============ Synthetic data foundry ============

CREATE TABLE IF NOT EXISTS synthetic_pairs (
    id               BIGSERIAL PRIMARY KEY,
    source_chunk_id  BIGINT REFERENCES chunks(id) ON DELETE SET NULL,
    prompt           TEXT NOT NULL,
    chosen           TEXT NOT NULL,
    rejected         TEXT,
    judge_score      DOUBLE PRECISION,
    judge_rationale  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
