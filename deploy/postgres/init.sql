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
