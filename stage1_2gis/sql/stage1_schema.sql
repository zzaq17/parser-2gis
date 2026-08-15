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
    primary_rubric text,
    is_advertised boolean NOT NULL DEFAULT false,
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

CREATE TABLE IF NOT EXISTS stage1_2gis.google_domain_snapshot (
    source_key text NOT NULL,
    normalized_domain text NOT NULL,
    source_row_number integer,
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_key, normalized_domain)
);

CREATE TABLE IF NOT EXISTS stage1_2gis.google_exports (
    normalized_domain text PRIMARY KEY,
    spreadsheet_id text NOT NULL,
    exported_at timestamptz NOT NULL DEFAULT now()
);

-- Canonicalize records created by older builds. Keep the original website_url
-- in company_domains, but merge domain keys such as www.example.ru into
-- example.ru so deduplication uses one stable value.
INSERT INTO stage1_2gis.company_domains (
    company_id, domain_id, website_url, first_seen_at, last_seen_at
)
SELECT
    link.company_id,
    canonical.domain_id,
    link.website_url,
    link.first_seen_at,
    link.last_seen_at
FROM stage1_2gis.domains AS legacy
JOIN stage1_2gis.domains AS canonical
  ON canonical.normalized_domain = substring(legacy.normalized_domain FROM 5)
JOIN stage1_2gis.company_domains AS link ON link.domain_id = legacy.domain_id
WHERE legacy.normalized_domain LIKE 'www.%'
ON CONFLICT (company_id, domain_id, website_url) DO UPDATE
SET first_seen_at = LEAST(stage1_2gis.company_domains.first_seen_at, EXCLUDED.first_seen_at),
    last_seen_at = GREATEST(stage1_2gis.company_domains.last_seen_at, EXCLUDED.last_seen_at);

DELETE FROM stage1_2gis.domains AS legacy
USING stage1_2gis.domains AS canonical
WHERE legacy.normalized_domain LIKE 'www.%'
  AND canonical.normalized_domain = substring(legacy.normalized_domain FROM 5);

UPDATE stage1_2gis.domains
SET normalized_domain = substring(normalized_domain FROM 5),
    last_seen_at = now()
WHERE normalized_domain LIKE 'www.%';

INSERT INTO stage1_2gis.google_domain_snapshot (
    source_key, normalized_domain, source_row_number, observed_at
)
SELECT
    source_key,
    substring(normalized_domain FROM 5),
    source_row_number,
    observed_at
FROM stage1_2gis.google_domain_snapshot
WHERE normalized_domain LIKE 'www.%'
ON CONFLICT (source_key, normalized_domain) DO UPDATE
SET source_row_number = COALESCE(
        EXCLUDED.source_row_number,
        stage1_2gis.google_domain_snapshot.source_row_number
    ),
    observed_at = GREATEST(stage1_2gis.google_domain_snapshot.observed_at, EXCLUDED.observed_at);

DELETE FROM stage1_2gis.google_domain_snapshot
WHERE normalized_domain LIKE 'www.%';

INSERT INTO stage1_2gis.google_exports (normalized_domain, spreadsheet_id, exported_at)
SELECT substring(normalized_domain FROM 5), spreadsheet_id, exported_at
FROM stage1_2gis.google_exports
WHERE normalized_domain LIKE 'www.%'
ON CONFLICT (normalized_domain) DO UPDATE
SET exported_at = GREATEST(stage1_2gis.google_exports.exported_at, EXCLUDED.exported_at);

DELETE FROM stage1_2gis.google_exports
WHERE normalized_domain LIKE 'www.%';

-- Keep existing deployments compatible when the schema is applied after upgrade.
ALTER TABLE stage1_2gis.branches ADD COLUMN IF NOT EXISTS primary_rubric text;
ALTER TABLE stage1_2gis.branches ADD COLUMN IF NOT EXISTS is_advertised boolean NOT NULL DEFAULT false;

-- Sheet-planned runs retain their business grouping independently of later
-- edits to the planning workbook. The full source snapshot remains in
-- input_snapshot_json.
ALTER TABLE stage1_2gis.runs ADD COLUMN IF NOT EXISTS task_vertical text;
ALTER TABLE stage1_2gis.runs ADD COLUMN IF NOT EXISTS task_subniche text;
ALTER TABLE stage1_2gis.runs ADD COLUMN IF NOT EXISTS sheet_task_batch_id uuid;
CREATE INDEX IF NOT EXISTS idx_stage1_sheet_task_runs
    ON stage1_2gis.runs (task_vertical, task_subniche, created_at DESC)
    WHERE command_id = 'sheet-tasks';
CREATE INDEX IF NOT EXISTS idx_stage1_sheet_task_batches
    ON stage1_2gis.runs (sheet_task_batch_id, created_at)
    WHERE command_id = 'sheet-tasks' AND sheet_task_batch_id IS NOT NULL;

-- Stable Stage 3 input: one highest-potential 2GIS branch per normalized domain.
CREATE OR REPLACE VIEW stage1_2gis.stage3_candidates AS
WITH ranked_candidates AS (
    SELECT DISTINCT ON (domain.normalized_domain)
        company_domain.website_url AS url,
        domain.normalized_domain AS domain,
        branch.name,
        branch.city,
        branch.primary_rubric AS rubric,
        branch.is_advertised::integer AS is_advertised
    FROM stage1_2gis.company_domains AS company_domain
    JOIN stage1_2gis.domains AS domain ON domain.domain_id = company_domain.domain_id
    JOIN stage1_2gis.company_branches AS company_branch ON company_branch.company_id = company_domain.company_id
    JOIN stage1_2gis.branches AS branch ON branch.branch_id = company_branch.branch_id
    WHERE NOT domain.is_excluded
    ORDER BY
        domain.normalized_domain,
        branch.is_advertised DESC,
        CASE WHEN lower(company_domain.website_url) LIKE 'https://%' THEN 0 ELSE 1 END,
        branch.last_seen_at DESC,
        company_domain.last_seen_at DESC,
        branch.branch_id
)
SELECT url, domain, name, city, rubric, is_advertised
FROM ranked_candidates
ORDER BY is_advertised DESC, domain;

CREATE OR REPLACE VIEW stage1_2gis.ready_candidates AS
SELECT candidate.*
FROM stage1_2gis.stage3_candidates AS candidate
WHERE NOT EXISTS (
    SELECT 1
    FROM stage1_2gis.google_domain_snapshot AS snapshot
    WHERE snapshot.normalized_domain = candidate.domain
)
AND NOT EXISTS (
    SELECT 1
    FROM stage1_2gis.google_exports AS export
    WHERE export.normalized_domain = candidate.domain
)
ORDER BY candidate.is_advertised DESC, candidate.domain;

-- One auditable row per planning run and canonical domain. Query and city keys
-- are resolved through the run snapshot by the application before publishing
-- them to the planning workbook.
CREATE OR REPLACE VIEW stage1_2gis.sheet_task_domain_results AS
SELECT
    run.run_id,
    run.task_vertical,
    run.task_subniche,
    domain.normalized_domain AS domain,
    min(company_domain.website_url) AS url,
    min(branch.name) AS company_name,
    min(branch.city) AS city,
    min(branch.primary_rubric) AS rubric,
    bool_or(branch.is_advertised) AS is_advertised,
    array_agg(DISTINCT job.query_key ORDER BY job.query_key) AS query_keys,
    array_agg(DISTINCT job.city_key ORDER BY job.city_key) AS city_keys,
    min(occurrence.received_at) AS found_at
FROM stage1_2gis.runs AS run
JOIN stage1_2gis.url_jobs AS job ON job.run_id = run.run_id
JOIN stage1_2gis.job_item_occurrences AS occurrence ON occurrence.job_id = job.job_id
JOIN stage1_2gis.branches AS branch ON branch.two_gis_item_id = occurrence.two_gis_item_id
JOIN stage1_2gis.company_branches AS company_branch ON company_branch.branch_id = branch.branch_id
JOIN stage1_2gis.company_domains AS company_domain ON company_domain.company_id = company_branch.company_id
JOIN stage1_2gis.domains AS domain ON domain.domain_id = company_domain.domain_id
WHERE run.command_id = 'sheet-tasks'
GROUP BY run.run_id, run.task_vertical, run.task_subniche, domain.normalized_domain;
