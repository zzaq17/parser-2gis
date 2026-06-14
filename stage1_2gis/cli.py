"""Command-line entrypoint for Stage 1 administration and workers."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid

from .browser import PlaywrightBrowserAdapter
from .config import ConfigurationError, PostgresSettings, WorkerSettings
from .persistence import Stage1Repository, build_connection_factory
from .worker import Stage1Worker, wait_until_stopped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stage1-2gis")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("migrate", help="Apply the Stage 1 PostgreSQL schema")

    create_run = commands.add_parser("create-run", help="Create an empty Stage 1 run")
    create_run.add_argument("--run-id", default=None)
    create_run.add_argument("--command-id", default=None)

    create_job = commands.add_parser("create-job", help="Add one URL job to an existing run")
    create_job.add_argument("--run-id", required=True)
    create_job.add_argument("--job-id", default=None)
    create_job.add_argument("--city-key", required=True)
    create_job.add_argument("--query-key", required=True)
    create_job.add_argument("--url", required=True)
    create_job.add_argument("--max-records", type=int, default=100)

    commands.add_parser("browser-worker", help="Run the PostgreSQL-backed browser worker")

    smoke = commands.add_parser("smoke", help="Create and synchronously process one live URL job")
    smoke.add_argument("--url", required=True)
    smoke.add_argument("--city-key", default="smoke")
    smoke.add_argument("--query-key", default="smoke")
    smoke.add_argument("--max-records", type=int, default=5)
    return parser


def _build_runtime() -> tuple[Stage1Repository, WorkerSettings, PlaywrightBrowserAdapter]:
    postgres = PostgresSettings.from_env()
    settings = WorkerSettings.from_env()
    repository = Stage1Repository(build_connection_factory(postgres))
    browser = PlaywrightBrowserAdapter(
        headed=settings.headed,
        disable_images=settings.disable_images,
        timeout_seconds=settings.browser_timeout_seconds,
        artifacts_dir=settings.artifacts_dir,
    )
    return repository, settings, browser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        repository, settings, browser = _build_runtime()
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 2

    if args.command == "migrate":
        repository.apply_schema()
        print(json.dumps({"status": "success", "operation": "migrate"}))
        return 0

    if args.command == "create-run":
        run_id = args.run_id or str(uuid.uuid4())
        repository.create_run(run_id=run_id, command_id=args.command_id)
        print(json.dumps({"status": "success", "run_id": run_id}))
        return 0

    if args.command == "create-job":
        job_id = args.job_id or str(uuid.uuid4())
        repository.create_job(
            job_id=job_id,
            run_id=args.run_id,
            city_key=args.city_key,
            query_key=args.query_key,
            source_url=args.url,
            max_records=args.max_records,
            max_attempts=settings.max_attempts,
        )
        print(json.dumps({"status": "success", "job_id": job_id, "run_id": args.run_id}))
        return 0

    worker = Stage1Worker(repository, browser, settings)
    if args.command == "browser-worker":
        wait_until_stopped(worker)
        return 0

    run_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    repository.create_run(run_id=run_id, command_id="smoke", snapshot={"url": args.url})
    repository.create_job(
        job_id=job_id,
        run_id=run_id,
        city_key=args.city_key,
        query_key=args.query_key,
        source_url=args.url,
        max_records=args.max_records,
        max_attempts=1,
    )
    job = repository.claim_job()
    if job is None:
        print(json.dumps({"status": "failed", "error": "Unable to claim smoke job"}))
        return 1
    result = worker.process_job(job)
    print(result.model_dump_json())
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
