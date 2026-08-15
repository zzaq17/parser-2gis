"""PostgreSQL queue and persistence implementation."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.resources import files
from typing import Any, Protocol

from .config import PostgresSettings
from .domain import canonicalize_domain
from .models import NormalizedItem, UrlJob


class Connection(Protocol):
    def cursor(self): ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


ConnectionFactory = Callable[[], Connection]


def build_connection_factory(settings: PostgresSettings) -> ConnectionFactory:
    def connect() -> Connection:
        import psycopg

        return psycopg.connect(settings.dsn)

    return connect


class Stage1Repository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _connection(self) -> Iterator[Connection]:
        connection = self._connection_factory()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def apply_schema(self) -> None:
        sql = files("stage1_2gis.sql").joinpath("stage1_schema.sql").read_text(encoding="utf-8")
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(sql)
            self._canonicalize_stored_domains(cursor)

    @staticmethod
    def _canonicalize_stored_domains(cursor: Any) -> None:
        """Merge legacy Punycode keys into their canonical Unicode records."""
        cursor.execute("SELECT domain_id, normalized_domain FROM stage1_2gis.domains ORDER BY first_seen_at, domain_id")
        domains = cursor.fetchall()
        canonical_ids = {
            domain: domain_id
            for domain_id, domain in domains
            if canonicalize_domain(domain) == domain
        }
        for domain_id, old_domain in domains:
            canonical = canonicalize_domain(old_domain)
            if canonical is None or canonical == old_domain:
                continue
            canonical_id = canonical_ids.get(canonical)
            if canonical_id is None:
                cursor.execute(
                    "UPDATE stage1_2gis.domains SET normalized_domain = %s, last_seen_at = now() WHERE domain_id = %s",
                    (canonical, domain_id),
                )
                canonical_ids[canonical] = domain_id
                continue
            cursor.execute(
                """
                UPDATE stage1_2gis.domains AS canonical
                SET first_seen_at = LEAST(canonical.first_seen_at, legacy.first_seen_at),
                    last_seen_at = GREATEST(canonical.last_seen_at, legacy.last_seen_at),
                    is_excluded = canonical.is_excluded OR legacy.is_excluded,
                    exclusion_reason = COALESCE(canonical.exclusion_reason, legacy.exclusion_reason)
                FROM stage1_2gis.domains AS legacy
                WHERE canonical.domain_id = %s AND legacy.domain_id = %s
                """,
                (canonical_id, domain_id),
            )
            cursor.execute(
                """
                INSERT INTO stage1_2gis.company_domains (
                    company_id, domain_id, website_url, first_seen_at, last_seen_at
                )
                SELECT company_id, %s, website_url, first_seen_at, last_seen_at
                FROM stage1_2gis.company_domains
                WHERE domain_id = %s
                ON CONFLICT (company_id, domain_id, website_url) DO UPDATE
                SET first_seen_at = LEAST(stage1_2gis.company_domains.first_seen_at, EXCLUDED.first_seen_at),
                    last_seen_at = GREATEST(stage1_2gis.company_domains.last_seen_at, EXCLUDED.last_seen_at)
                """,
                (canonical_id, domain_id),
            )
            cursor.execute("DELETE FROM stage1_2gis.company_domains WHERE domain_id = %s", (domain_id,))
            cursor.execute("DELETE FROM stage1_2gis.domains WHERE domain_id = %s", (domain_id,))

        cursor.execute(
            "SELECT source_key, normalized_domain, source_row_number, observed_at FROM stage1_2gis.google_domain_snapshot"
        )
        snapshot_rows = cursor.fetchall()
        cursor.execute("DELETE FROM stage1_2gis.google_domain_snapshot")
        normalized_snapshot = [
            (source_key, canonical, row_number, observed_at)
            for source_key, domain, row_number, observed_at in snapshot_rows
            if (canonical := canonicalize_domain(domain)) is not None
        ]
        if normalized_snapshot:
            cursor.executemany(
                """
                INSERT INTO stage1_2gis.google_domain_snapshot (
                    source_key, normalized_domain, source_row_number, observed_at
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (source_key, normalized_domain) DO UPDATE
                SET source_row_number = COALESCE(
                        EXCLUDED.source_row_number,
                        stage1_2gis.google_domain_snapshot.source_row_number
                    ),
                    observed_at = GREATEST(stage1_2gis.google_domain_snapshot.observed_at, EXCLUDED.observed_at)
                """,
                normalized_snapshot,
            )

        cursor.execute("SELECT normalized_domain, spreadsheet_id, exported_at FROM stage1_2gis.google_exports")
        export_rows = cursor.fetchall()
        cursor.execute("DELETE FROM stage1_2gis.google_exports")
        normalized_exports = [
            (canonical, spreadsheet_id, exported_at)
            for domain, spreadsheet_id, exported_at in export_rows
            if (canonical := canonicalize_domain(domain)) is not None
        ]
        if normalized_exports:
            cursor.executemany(
                """
                INSERT INTO stage1_2gis.google_exports (normalized_domain, spreadsheet_id, exported_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (normalized_domain) DO UPDATE
                SET exported_at = GREATEST(stage1_2gis.google_exports.exported_at, EXCLUDED.exported_at)
                """,
                normalized_exports,
            )

    def replace_google_domain_snapshot(self, rows: list[tuple[str, str, int | None]]) -> int:
        """Replace the three Google-domain sources with one auditable snapshot."""
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM stage1_2gis.google_domain_snapshot")
            if rows:
                cursor.executemany(
                    """
                    INSERT INTO stage1_2gis.google_domain_snapshot (source_key, normalized_domain, source_row_number)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (source_key, normalized_domain) DO UPDATE
                    SET source_row_number = EXCLUDED.source_row_number, observed_at = now()
                    """,
                    rows,
                )
        return len(rows)

    def list_ready_candidates(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT url, domain, name, city, rubric, is_advertised FROM stage1_2gis.ready_candidates"
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT %s"
            params = (limit,)
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(sql, params)
            return [
                dict(zip(("url", "domain", "name", "city", "rubric", "is_advertised"), row, strict=True))
                for row in cursor.fetchall()
            ]

    def mark_google_exports(self, *, spreadsheet_id: str, domains: list[str]) -> None:
        canonical_domains = list(dict.fromkeys(
            canonical
            for domain in domains
            if (canonical := canonicalize_domain(domain)) is not None
        ))
        if not canonical_domains:
            return
        with self._connection() as connection:
            connection.cursor().executemany(
                """
                INSERT INTO stage1_2gis.google_exports (normalized_domain, spreadsheet_id)
                VALUES (%s, %s)
                ON CONFLICT (normalized_domain) DO NOTHING
                """,
                [(domain, spreadsheet_id) for domain in canonical_domains],
            )

    def get_run_status(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT run.status, run.generated_url_count, run.completed_url_count, run.failed_url_count,
                       run.companies_count, run.domains_count, run.started_at, run.finished_at,
                       count(job.job_id) FILTER (WHERE job.status = 'queued') AS queued_jobs,
                       count(job.job_id) FILTER (WHERE job.status = 'running') AS running_jobs,
                       count(job.job_id) FILTER (WHERE job.status = 'retry_wait') AS retry_jobs,
                       count(job.job_id) FILTER (WHERE job.status = 'partial') AS partial_jobs,
                       coalesce(sum(job.items_received), 0) AS items_received
                FROM stage1_2gis.runs AS run
                LEFT JOIN stage1_2gis.url_jobs AS job ON job.run_id = run.run_id
                WHERE run.run_id = %s
                GROUP BY run.run_id
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        columns = (
            "status", "generated_jobs", "completed_jobs", "failed_jobs", "companies", "domains",
            "started_at", "finished_at", "queued_jobs", "running_jobs", "retry_jobs", "partial_jobs", "items_received",
        )
        result = dict(zip(columns, row, strict=True))
        total = result["generated_jobs"]
        result["progress_percent"] = round(100 * (result["completed_jobs"] + result["failed_jobs"]) / total, 1) if total else 0.0
        return result

    def create_run(
        self,
        *,
        run_id: str,
        command_id: str | None = None,
        snapshot: dict[str, Any] | None = None,
        task_vertical: str | None = None,
        task_subniche: str | None = None,
        sheet_task_batch_id: str | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.cursor().execute(
                """
                INSERT INTO stage1_2gis.runs (
                    run_id, command_id, status, input_snapshot_json, task_vertical, task_subniche, sheet_task_batch_id
                ) VALUES (%s, %s, 'queued', %s, %s, %s, %s::uuid)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    run_id,
                    command_id,
                    json.dumps(snapshot or {}, ensure_ascii=False),
                    task_vertical,
                    task_subniche,
                    sheet_task_batch_id,
                ),
            )

    def get_latest_sheet_task_batch(self) -> tuple[str, list[dict[str, Any]]] | None:
        """Return the runs created by the latest resumable sheet-tasks launch."""
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                WITH latest_batch AS (
                    SELECT sheet_task_batch_id
                    FROM stage1_2gis.runs
                    WHERE command_id = 'sheet-tasks'
                      AND sheet_task_batch_id IS NOT NULL
                    ORDER BY created_at DESC, run_id DESC
                    LIMIT 1
                )
                SELECT run.sheet_task_batch_id, run.run_id, run.status
                FROM stage1_2gis.runs AS run
                JOIN latest_batch ON latest_batch.sheet_task_batch_id = run.sheet_task_batch_id
                ORDER BY run.created_at, run.run_id
                """
            )
            rows = cursor.fetchall()
        if not rows:
            return None
        return str(rows[0][0]), [
            dict(zip(("run_id", "status"), row[1:], strict=True)) for row in rows
        ]

    def list_sheet_task_runs(self) -> list[dict[str, Any]]:
        """Return every planning run; callers choose the latest task per niche."""
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT run_id, task_vertical, task_subniche, status, generated_url_count,
                       completed_url_count, failed_url_count, companies_count, domains_count,
                       started_at, finished_at, error_summary, created_at, input_snapshot_json
                FROM stage1_2gis.runs
                WHERE command_id = 'sheet-tasks'
                ORDER BY created_at DESC, run_id DESC
                """
            )
            columns = (
                "run_id", "vertical", "subniche", "status", "generated_jobs", "completed_jobs",
                "failed_jobs", "companies", "domains", "started_at", "finished_at", "error_summary",
                "created_at", "snapshot",
            )
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def list_sheet_task_results(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT run_id, task_vertical, task_subniche, domain, url, company_name, city,
                       rubric, is_advertised, query_keys, city_keys, found_at
                FROM stage1_2gis.sheet_task_domain_results
                ORDER BY found_at, run_id, domain
                """
            )
            columns = (
                "run_id", "vertical", "subniche", "domain", "url", "company_name", "city",
                "rubric", "is_advertised", "query_keys", "city_keys", "found_at",
            )
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def create_job(
        self,
        *,
        job_id: str,
        run_id: str,
        city_key: str,
        query_key: str,
        source_url: str,
        max_records: int,
        max_attempts: int,
    ) -> None:
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO stage1_2gis.url_jobs (
                    job_id, run_id, city_key, query_key, source_url, max_records, max_attempts
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, city_key, query_key, source_url) DO NOTHING
                """,
                (job_id, run_id, city_key, query_key, source_url, max_records, max_attempts),
            )
            cursor.execute(
                """
                UPDATE stage1_2gis.runs
                SET generated_url_count = (
                    SELECT count(*) FROM stage1_2gis.url_jobs WHERE run_id = %s
                ), updated_at = now()
                WHERE run_id = %s
                """,
                (run_id, run_id),
            )

    def recover_stale_jobs(self, stale_seconds: int) -> int:
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE stage1_2gis.url_jobs
                SET status = CASE WHEN attempt_no < max_attempts THEN 'retry_wait' ELSE 'failed' END,
                    next_attempt_at = CASE WHEN attempt_no < max_attempts THEN now() ELSE NULL END,
                    lock_token = NULL,
                    error_code = 'stale_worker',
                    error_message = 'Worker heartbeat expired',
                    updated_at = now()
                WHERE status = 'running'
                  AND heartbeat_at < now() - (%s * interval '1 second')
                """,
                (stale_seconds,),
            )
            recovered = cursor.rowcount
            cursor.execute(
                """
                UPDATE stage1_2gis.runs run
                SET status = 'completed_with_errors',
                    finished_at = now(),
                    updated_at = now(),
                    error_summary = COALESCE(error_summary, 'One or more browser workers exceeded their heartbeat lease')
                WHERE run.status IN ('queued', 'running')
                  AND EXISTS (
                      SELECT 1 FROM stage1_2gis.url_jobs job
                      WHERE job.run_id = run.run_id AND job.status = 'failed'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM stage1_2gis.url_jobs job
                      WHERE job.run_id = run.run_id AND job.status IN ('queued', 'running', 'retry_wait')
                  )
                """
            )
            return recovered

    def resume_run(self, run_id: str, *, retry_errors: bool = True) -> dict[str, int] | None:
        """Prepare an interrupted run for immediate processing.

        Completed jobs are deliberately left untouched. Running jobs are safe to
        release here because this is an explicit operator action for a stopped
        run. Failed and partial jobs get a fresh attempt budget when requested.
        """
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT 1 FROM stage1_2gis.runs WHERE run_id = %s FOR UPDATE",
                (run_id,),
            )
            if cursor.fetchone() is None:
                return None

            cursor.execute(
                """
                UPDATE stage1_2gis.url_jobs
                SET status = 'queued',
                    attempt_no = CASE
                        WHEN status IN ('failed', 'partial') THEN 0
                        ELSE attempt_no
                    END,
                    lock_token = NULL,
                    heartbeat_at = NULL,
                    next_attempt_at = NULL,
                    finished_at = NULL,
                    error_code = NULL,
                    error_message = NULL,
                    updated_at = now()
                WHERE run_id = %s
                  AND (
                      status IN ('running', 'retry_wait')
                      OR (%s AND status IN ('failed', 'partial'))
                  )
                """,
                (run_id, retry_errors),
            )
            requeued = cursor.rowcount
            cursor.execute(
                """
                UPDATE stage1_2gis.runs
                SET status = CASE
                        WHEN EXISTS (
                            SELECT 1 FROM stage1_2gis.url_jobs
                            WHERE run_id = %s AND status IN ('queued', 'running', 'retry_wait')
                        ) THEN 'queued'
                        ELSE status
                    END,
                    completed_url_count = (
                        SELECT count(*) FROM stage1_2gis.url_jobs
                        WHERE run_id = %s AND status = 'completed'
                    ),
                    failed_url_count = (
                        SELECT count(*) FROM stage1_2gis.url_jobs
                        WHERE run_id = %s AND status IN ('failed', 'partial')
                    ),
                    finished_at = NULL,
                    error_summary = NULL,
                    updated_at = now()
                WHERE run_id = %s
                """,
                (run_id, run_id, run_id, run_id),
            )
            cursor.execute(
                """
                SELECT
                    count(*) FILTER (WHERE status = 'queued'),
                    count(*) FILTER (WHERE status = 'completed'),
                    count(*) FILTER (WHERE status IN ('failed', 'partial'))
                FROM stage1_2gis.url_jobs
                WHERE run_id = %s
                """,
                (run_id,),
            )
            queued, completed, failed = cursor.fetchone()
        return {
            "requeued_jobs": requeued,
            "queued_jobs": queued,
            "completed_jobs": completed,
            "failed_jobs": failed,
        }

    def halt_run(self, run_id: str, reason: str) -> None:
        with self._connection() as connection:
            connection.cursor().execute(
                """
                UPDATE stage1_2gis.runs
                SET status = 'halted',
                    error_summary = %s,
                    finished_at = now(),
                    updated_at = now()
                WHERE run_id = %s
                """,
                (reason, run_id),
            )

    def claim_job(self, run_id: str | None = None) -> UrlJob | None:
        lock_token = str(uuid.uuid4())
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT job_id
                    FROM stage1_2gis.url_jobs
                    WHERE status IN ('queued', 'retry_wait')
                      AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                      AND attempt_no < max_attempts
                      AND (%s::uuid IS NULL OR run_id = %s::uuid)
                    ORDER BY priority ASC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE stage1_2gis.url_jobs job
                SET status = 'running',
                    attempt_no = attempt_no + 1,
                    lock_token = %s,
                    heartbeat_at = now(),
                    started_at = COALESCE(started_at, now()),
                    error_code = NULL,
                    error_message = NULL,
                    updated_at = now()
                FROM candidate
                WHERE job.job_id = candidate.job_id
                RETURNING job.job_id, job.run_id, job.city_key, job.query_key,
                          job.source_url, job.max_records, job.attempt_no, job.lock_token
                """,
                (run_id, run_id, lock_token),
            )
            row = cursor.fetchone()
            if row is not None:
                cursor.execute(
                    """
                    UPDATE stage1_2gis.runs
                    SET status = 'running',
                        started_at = COALESCE(started_at, now()),
                        updated_at = now()
                    WHERE run_id = %s
                    """,
                    (row[1],),
                )
        if row is None:
            return None
        return UrlJob(
            job_id=str(row[0]),
            run_id=str(row[1]),
            city_key=row[2],
            query_key=row[3],
            source_url=row[4],
            max_records=row[5],
            attempt_no=row[6],
            lock_token=str(row[7]),
        )

    def heartbeat(self, job: UrlJob) -> None:
        with self._connection() as connection:
            connection.cursor().execute(
                """
                UPDATE stage1_2gis.url_jobs
                SET heartbeat_at = now(), updated_at = now()
                WHERE job_id = %s AND lock_token = %s AND status = 'running'
                """,
                (job.job_id, job.lock_token),
            )

    def persist_item(self, job: UrlJob, document: dict[str, Any], item: NormalizedItem) -> None:
        payload_json = json.dumps(document, ensure_ascii=False, sort_keys=True)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        branch_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"2gis-branch:{item.two_gis_item_id}"))
        company_key = item.two_gis_org_id or f"branch:{item.two_gis_item_id}"
        company_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"2gis-company:{company_key}"))

        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO stage1_2gis.raw_items (
                    two_gis_item_id, payload_json, payload_hash, first_seen_run_id, last_seen_run_id
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (two_gis_item_id) DO UPDATE
                SET payload_json = EXCLUDED.payload_json,
                    payload_hash = EXCLUDED.payload_hash,
                    last_seen_at = now(),
                    last_seen_run_id = EXCLUDED.last_seen_run_id
                """,
                (item.two_gis_item_id, payload_json, payload_hash, job.run_id, job.run_id),
            )
            cursor.execute(
                """
                INSERT INTO stage1_2gis.branches (
                    branch_id, two_gis_item_id, two_gis_org_id, name, description, address, city,
                    primary_rubric, is_advertised, phones_json, emails_json, two_gis_url, normalized_payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (two_gis_item_id) DO UPDATE
                SET two_gis_org_id = EXCLUDED.two_gis_org_id,
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    address = EXCLUDED.address,
                    city = EXCLUDED.city,
                    primary_rubric = EXCLUDED.primary_rubric,
                    is_advertised = EXCLUDED.is_advertised,
                    phones_json = EXCLUDED.phones_json,
                    emails_json = EXCLUDED.emails_json,
                    normalized_payload = EXCLUDED.normalized_payload,
                    last_seen_at = now()
                """,
                (
                    branch_id,
                    item.two_gis_item_id,
                    item.two_gis_org_id,
                    item.name,
                    item.description,
                    item.address,
                    item.city,
                    item.primary_rubric,
                    item.is_advertised,
                    json.dumps(item.phones, ensure_ascii=False),
                    json.dumps(item.emails, ensure_ascii=False),
                    item.two_gis_url,
                    json.dumps(item.normalized_payload, ensure_ascii=False),
                ),
            )
            cursor.execute(
                """
                INSERT INTO stage1_2gis.companies (company_id, two_gis_org_id, display_name, dedup_key)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (dedup_key) DO UPDATE
                SET display_name = COALESCE(EXCLUDED.display_name, stage1_2gis.companies.display_name),
                    two_gis_org_id = COALESCE(EXCLUDED.two_gis_org_id, stage1_2gis.companies.two_gis_org_id),
                    last_seen_at = now()
                """,
                (company_id, item.two_gis_org_id, item.name, company_key),
            )
            cursor.execute(
                """
                INSERT INTO stage1_2gis.company_branches (company_id, branch_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
                """,
                (company_id, branch_id),
            )
            for website, domain in item.website_domains:
                domain_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, domain))
                cursor.execute(
                    """
                    INSERT INTO stage1_2gis.domains (domain_id, normalized_domain)
                    VALUES (%s, %s)
                    ON CONFLICT (normalized_domain) DO UPDATE SET last_seen_at = now()
                    RETURNING domain_id
                    """,
                    (domain_id, domain),
                )
                stored_domain_id = cursor.fetchone()[0]
                cursor.execute(
                    """
                    INSERT INTO stage1_2gis.company_domains (company_id, domain_id, website_url)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (company_id, domain_id, website_url)
                    DO UPDATE SET last_seen_at = now()
                    """,
                    (company_id, stored_domain_id, website),
                )
            cursor.execute(
                """
                INSERT INTO stage1_2gis.job_item_occurrences (job_id, two_gis_item_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
                """,
                (job.job_id, item.two_gis_item_id),
            )
            cursor.execute(
                """
                UPDATE stage1_2gis.url_jobs
                SET items_received = (
                    SELECT count(*) FROM stage1_2gis.job_item_occurrences WHERE job_id = %s
                ), heartbeat_at = now(), updated_at = now()
                WHERE job_id = %s AND lock_token = %s
                """,
                (job.job_id, job.job_id, job.lock_token),
            )

    def finish_job(
        self,
        job: UrlJob,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        retry_delay_seconds: int = 30,
    ) -> None:
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE stage1_2gis.url_jobs
                SET status = %s,
                    lock_token = NULL,
                    heartbeat_at = now(),
                    finished_at = CASE WHEN %s IN ('completed', 'partial', 'failed', 'cancelled') THEN now() ELSE NULL END,
                    next_attempt_at = CASE WHEN %s = 'retry_wait'
                        THEN now() + (%s * interval '1 second') ELSE NULL END,
                    error_code = %s,
                    error_message = %s,
                    updated_at = now()
                WHERE job_id = %s AND lock_token = %s
                """,
                (
                    status,
                    status,
                    status,
                    retry_delay_seconds,
                    error_code,
                    error_message,
                    job.job_id,
                    job.lock_token,
                ),
            )
            cursor.execute(
                """
                UPDATE stage1_2gis.runs
                SET status = CASE
                        WHEN EXISTS (
                            SELECT 1 FROM stage1_2gis.url_jobs
                            WHERE run_id = %s AND status IN ('queued', 'running', 'retry_wait')
                        ) THEN 'running'
                        WHEN EXISTS (
                            SELECT 1 FROM stage1_2gis.url_jobs
                            WHERE run_id = %s AND status IN ('failed', 'partial')
                        ) THEN 'completed_with_errors'
                        ELSE 'completed'
                    END,
                    completed_url_count = (
                        SELECT count(*) FROM stage1_2gis.url_jobs WHERE run_id = %s AND status = 'completed'
                    ),
                    failed_url_count = (
                        SELECT count(*) FROM stage1_2gis.url_jobs WHERE run_id = %s AND status IN ('failed', 'partial')
                    ),
                    companies_count = (
                        SELECT count(DISTINCT cb.company_id)
                        FROM stage1_2gis.company_branches cb
                        JOIN stage1_2gis.job_item_occurrences occurrence ON occurrence.two_gis_item_id = (
                            SELECT branch.two_gis_item_id FROM stage1_2gis.branches branch
                            WHERE branch.branch_id = cb.branch_id
                        )
                        JOIN stage1_2gis.url_jobs job ON job.job_id = occurrence.job_id
                        WHERE job.run_id = %s
                    ),
                    domains_count = (
                        SELECT count(DISTINCT cd.domain_id)
                        FROM stage1_2gis.company_domains cd
                        JOIN stage1_2gis.company_branches cb ON cb.company_id = cd.company_id
                        JOIN stage1_2gis.branches branch ON branch.branch_id = cb.branch_id
                        JOIN stage1_2gis.job_item_occurrences occurrence
                          ON occurrence.two_gis_item_id = branch.two_gis_item_id
                        JOIN stage1_2gis.url_jobs job ON job.job_id = occurrence.job_id
                        WHERE job.run_id = %s
                    ),
                    finished_at = CASE WHEN NOT EXISTS (
                        SELECT 1 FROM stage1_2gis.url_jobs
                        WHERE run_id = %s AND status IN ('queued', 'running', 'retry_wait')
                    ) THEN now() ELSE NULL END,
                    updated_at = now()
                WHERE run_id = %s
                """,
                (job.run_id,) * 8,
            )
