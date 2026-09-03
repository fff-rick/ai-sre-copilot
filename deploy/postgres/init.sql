CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS investigations (
    investigation_id text PRIMARY KEY,
    investigation jsonb NOT NULL,
    status text NOT NULL,
    report jsonb,
    cancel_requested boolean NOT NULL DEFAULT false,
    run_requested boolean NOT NULL DEFAULT true,
    lease_owner text,
    lease_expires timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS investigations_claim_idx
ON investigations (run_requested, status, lease_expires, created_at);

CREATE TABLE IF NOT EXISTS investigation_events (
    event_id bigserial PRIMARY KEY,
    investigation_id text NOT NULL REFERENCES investigations(investigation_id) ON DELETE CASCADE,
    event_type text NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS investigation_events_stream_idx
ON investigation_events (investigation_id, event_id);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    document_id text PRIMARY KEY,
    source_id text NOT NULL UNIQUE,
    title text NOT NULL,
    document_type text NOT NULL CHECK (document_type IN ('runbook', 'service', 'incident')),
    service text,
    environment text,
    version text NOT NULL,
    valid_from timestamptz,
    valid_until timestamptz,
    source_ref text NOT NULL,
    content_hash text NOT NULL,
    imported_at timestamptz NOT NULL,
    CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from < valid_until)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    heading text,
    content text NOT NULL,
    content_hash text NOT NULL,
    embedding vector NOT NULL,
    embedding_dimensions integer NOT NULL CHECK (embedding_dimensions > 0),
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(heading, '') || ' ' || content)
    ) STORED,
    UNIQUE (document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS knowledge_chunks_search_idx
ON knowledge_chunks USING gin (search_vector);

CREATE INDEX IF NOT EXISTS knowledge_documents_filter_idx
ON knowledge_documents (service, environment, document_type, valid_from, valid_until);
