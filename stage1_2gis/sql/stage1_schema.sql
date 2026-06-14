CREATE SCHEMA IF NOT EXISTS stage1_2gis;

CREATE TABLE IF NOT EXISTS stage1_2gis.runs (
    run_id uuid PRIMARY KEY,
    command_id text,
    status text NOT NULL,
    input_snapshot_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    generated_url_count integer NOT NULL DEFAULT 0,
    completed_url_count integer NOT NULL DEFAULT 0,
    failed_url_count integer NOT NULL DEFAULT 0,
    companies_count integer NOT NULL DEFAULT 0,
    domains_count integer NOT NULL DEFAULT 0,
    worker_build text,
    error_summary text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stage1_2gis.url_jobs (
    job_id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES stage1_2gis.runs(run_id) ON DELETE CASCADE,
    city_key text NOT NULL,
    query_key text NOT NULL,
    source_url text NOT NULL,
    max_records integer NOT NULL CHECK (max_records > 0),
    priority integer NOT NULL DEFAULT 100,
    status text NOT NULL DEFAULT 'queued',
    attempt_no integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,
    lock_token uuid,
    heartbeat_at timestamptz,
    next_attempt_at timestamptz,
    items_received integer NOT NULL DEFAULT 0,
    started_at timestamptz,
    finished_at timestamptz,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, city_key, query_key, source_url)
);

CREATE INDEX IF NOT EXISTS idx_stage1_jobs_claim
    ON stage1_2gis.url_jobs (status, next_attempt_at, priority, created_at);

CREATE TABLE IF NOT EXISTS stage1_2gis.raw_items (
    two_gis_item_id text PRIMARY KEY,
    payload_json jsonb NOT NULL,
    payload_hash text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    first_seen_run_id uuid NOT NULL REFERENCES stage1_2gis.runs(run_id),
    last_seen_run_id uuid NOT NULL REFERENCES stage1_2gis.runs(run_id)
);

CREATE TABLE IF NOT EXISTS stage1_2gis.branches (
    branch_id uuid PRIMARY KEY,
    two_gis_item_id text NOT NULL UNIQUE REFERENCES stage1_2gis.raw_items(two_gis_item_id),
    two_gis_org_id text,
    name text,
    description text,
    address text,
    city text,
    phones_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    emails_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    two_gis_url text NOT NULL,
    normalized_payload jsonb NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stage1_2gis.companies (
    company_id uuid PRIMARY KEY,
    two_gis_org_id text UNIQUE,
    display_name text,
    dedup_key text NOT NULL UNIQUE,
    dedup_confidence numeric(5,2) NOT NULL DEFAULT 1,
    manual_review_required boolean NOT NULL DEFAULT false,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stage1_2gis.domains (
    domain_id uuid PRIMARY KEY,
    normalized_domain text NOT NULL UNIQUE,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    is_excluded boolean NOT NULL DEFAULT false,
    exclusion_reason text
);

CREATE TABLE IF NOT EXISTS stage1_2gis.company_branches (
    company_id uuid NOT NULL REFERENCES stage1_2gis.companies(company_id) ON DELETE CASCADE,
    branch_id uuid NOT NULL REFERENCES stage1_2gis.branches(branch_id) ON DELETE CASCADE,
    PRIMARY KEY (company_id, branch_id)
);

CREATE TABLE IF NOT EXISTS stage1_2gis.company_domains (
    company_id uuid NOT NULL REFERENCES stage1_2gis.companies(company_id) ON DELETE CASCADE,
    domain_id uuid NOT NULL REFERENCES stage1_2gis.domains(domain_id) ON DELETE CASCADE,
    website_url text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, domain_id, website_url)
);

CREATE TABLE IF NOT EXISTS stage1_2gis.job_item_occurrences (
    job_id uuid NOT NULL REFERENCES stage1_2gis.url_jobs(job_id) ON DELETE CASCADE,
    two_gis_item_id text NOT NULL REFERENCES stage1_2gis.raw_items(two_gis_item_id) ON DELETE CASCADE,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, two_gis_item_id)
);
