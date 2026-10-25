CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS datasets (
    id text PRIMARY KEY,
    name text NOT NULL,
    digest text NOT NULL,
    case_count integer NOT NULL,
    path text NOT NULL,
    source text NOT NULL DEFAULT 'register',
    manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS comparisons (
    id text PRIMARY KEY,
    name text NOT NULL,
    status text NOT NULL,
    stage text,
    repo_url text NOT NULL,
    commit_h1 text NOT NULL,
    commit_h2 text NOT NULL,
    commit_h3 text NOT NULL,
    dataset_id text NOT NULL REFERENCES datasets(id),
    dataset_name text,
    dataset_digest text,
    models jsonb NOT NULL,
    trials integer NOT NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    progress jsonb NOT NULL DEFAULT '{}'::jsonb,
    error jsonb,
    summary jsonb,
    cancel_requested boolean NOT NULL DEFAULT false,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);

CREATE INDEX IF NOT EXISTS comparisons_status_created_idx
    ON comparisons (status, created_at DESC);

CREATE TABLE IF NOT EXISTS harness_runs (
    id bigserial PRIMARY KEY,
    comparison_id text NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
    harness_id text NOT NULL,
    commit text NOT NULL,
    image text,
    container_name text,
    status text NOT NULL,
    log_tail text,
    error jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (comparison_id, harness_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id text PRIMARY KEY,
    comparison_id text NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
    harness_id text NOT NULL,
    model text NOT NULL,
    case_id text NOT NULL,
    trial integer NOT NULL,
    status text NOT NULL,
    request_id text,
    attempts integer NOT NULL DEFAULT 0,
    response jsonb,
    evaluation jsonb,
    error jsonb,
    usage jsonb,
    guardrails jsonb,
    budget jsonb,
    latency_ms integer,
    dispatched_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (comparison_id, harness_id, model, case_id, trial)
);

CREATE INDEX IF NOT EXISTS jobs_comparison_status_idx
    ON jobs (comparison_id, status);
CREATE INDEX IF NOT EXISTS jobs_comparison_harness_model_idx
    ON jobs (comparison_id, harness_id, model);

CREATE TABLE IF NOT EXISTS events (
    id bigserial PRIMARY KEY,
    comparison_id text NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
    type text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS events_comparison_created_idx
    ON events (comparison_id, created_at DESC);
