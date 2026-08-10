"""Command-line entrypoint for Stage 1 administration and workers."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path

from .browser import PlaywrightBrowserAdapter
from .config import ConfigurationError, PostgresSettings, SheetTaskSettings, WorkerSettings, runtime_env
from .google_sheets import GoogleSheetsQueueClient
from .persistence import Stage1Repository, build_connection_factory
from .sheet_tasks import (
    SheetTaskError,
    execute_marked_sheet_tasks,
    load_city_catalog,
    load_city_groups,
    sync_task_workbook,
)
from .worker import RunHaltedError, Stage1Worker, wait_until_stopped


def build_parser(env: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    source = runtime_env(env)
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
    process_run = commands.add_parser("process-run", help="Process queued browser jobs from one run, then exit")
    process_run.add_argument("--run-id", required=True)
    resume_run = commands.add_parser(
        "resume-run",
        help="Resume interrupted jobs and retry failed/partial jobs from one run",
    )
    resume_run.add_argument("--run-id", required=True)
    resume_run.add_argument(
        "--skip-errors",
        action="store_true",
        help="Continue interrupted and queued jobs without retrying failed/partial jobs",
    )
    resume_run.add_argument(
        "--prepare-only",
        action="store_true",
        help="Requeue jobs but do not start browser processing",
    )
    run_status = commands.add_parser("run-status", help="Print persisted progress and result counts for one run")
    run_status.add_argument("--run-id", required=True)

    google_sync = commands.add_parser("sync-google-domains", help="Snapshot Google Sheets domains for Stage 1 deduplication")
    google_sync.add_argument("--spreadsheet-id", default=source.get("STAGE1_SPREADSHEET_ID"))
    google_sync.add_argument("--input-sheet", default=source.get("STAGE1_INPUT_SHEET", "Ввод"))
    google_sync.add_argument("--new-domains-sheet", default=source.get("STAGE1_NEW_DOMAINS_SHEET", "NEW domains"))
    google_sync.add_argument("--credentials-path", default=source.get("GOOGLE_APPLICATION_CREDENTIALS"))

    google_export = commands.add_parser("export-ready-candidates", help="Append deduplicated Stage 1 candidates to NEW domains")
    google_export.add_argument("--spreadsheet-id", default=source.get("STAGE1_SPREADSHEET_ID"))
    google_export.add_argument("--new-domains-sheet", default=source.get("STAGE1_NEW_DOMAINS_SHEET", "NEW domains"))
    google_export.add_argument("--credentials-path", default=source.get("GOOGLE_APPLICATION_CREDENTIALS"))
    google_export.add_argument("--limit", type=int, default=None)
    google_export.add_argument("--apply", action="store_true", help="Write rows to Google Sheets; otherwise print the preview")

    for command_name, help_text in (
        ("init-sheet-tasks", "Create and initialize the separate 2GIS planning sheets"),
        ("sheet-tasks", "Preview or process marked 2GIS sheet tasks"),
        ("sync-sheet-tasks", "Refresh planning-sheet summaries and results from PostgreSQL"),
    ):
        task_command = commands.add_parser(command_name, help=help_text)
        task_command.add_argument("--credentials-path", default=source.get("GOOGLE_APPLICATION_CREDENTIALS"))
        if command_name == "init-sheet-tasks":
            task_command.add_argument(
                "--cities-list",
                default=str(Path(__file__).resolve().parents[2] / "tasks" / "cities_list.json"),
            )
            task_command.add_argument("--apply", action="store_true", help="Create the three managed tabs")
        elif command_name == "sheet-tasks":
            task_command.add_argument("--apply", action="store_true", help="Create and process the marked task runs")

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

    if args.command == "sync-google-domains":
        if not args.spreadsheet_id:
            print("Missing spreadsheet ID: --spreadsheet-id or STAGE1_SPREADSHEET_ID", file=sys.stderr)
            return 2
        if not args.credentials_path:
            print("Missing Google service account path: --credentials-path or GOOGLE_APPLICATION_CREDENTIALS", file=sys.stderr)
            return 2
        client = GoogleSheetsQueueClient.from_service_account(args.credentials_path)
        count = repository.replace_google_domain_snapshot(client.read_snapshot_rows(args.spreadsheet_id, input_sheet=args.input_sheet, new_domains_sheet=args.new_domains_sheet))
        print(json.dumps({"status": "success", "snapshot_domains": count}, ensure_ascii=False))
        return 0

    if args.command == "export-ready-candidates":
        if not args.spreadsheet_id:
            print("Missing spreadsheet ID: --spreadsheet-id or STAGE1_SPREADSHEET_ID", file=sys.stderr)
            return 2
        if args.limit is not None and args.limit <= 0:
            print("--limit must be greater than zero", file=sys.stderr)
            return 2
        candidates = repository.list_ready_candidates(args.limit)
        if not args.apply:
            print(json.dumps({"status": "preview", "candidate_count": len(candidates), "candidates": candidates}, ensure_ascii=False))
            return 0
        if not args.credentials_path:
            print("Missing Google service account path: --credentials-path or GOOGLE_APPLICATION_CREDENTIALS", file=sys.stderr)
            return 2
        client = GoogleSheetsQueueClient.from_service_account(args.credentials_path)
        exported = client.append_candidates(args.spreadsheet_id, new_domains_sheet=args.new_domains_sheet, rows=candidates)
        repository.mark_google_exports(spreadsheet_id=args.spreadsheet_id, domains=[candidate["domain"] for candidate in candidates])
        print(json.dumps({"status": "success", "exported_count": exported}, ensure_ascii=False))
        return 0

    if args.command in {"init-sheet-tasks", "sheet-tasks", "sync-sheet-tasks"}:
        if not args.credentials_path:
            print("Missing Google service account path: --credentials-path or GOOGLE_APPLICATION_CREDENTIALS", file=sys.stderr)
            return 2
        try:
            task_settings = SheetTaskSettings.from_env()
            client = GoogleSheetsQueueClient.from_service_account(args.credentials_path)
            if args.command == "init-sheet-tasks":
                cities = load_city_groups(Path(args.cities_list))
                if not args.apply:
                    print(json.dumps({"status": "preview", "sheets": [task_settings.cities_sheet, task_settings.summary_sheet, task_settings.results_sheet], "cities": len(cities)}, ensure_ascii=False, indent=2))
                    return 0
                client.initialize_task_sheets(task_settings, cities)
                repository.apply_schema()
                sync_task_workbook(client, repository, task_settings)
                print(json.dumps({"status": "success", "operation": "init-sheet-tasks", "cities": len(cities)}, ensure_ascii=False, indent=2))
                return 0
            if args.command == "sync-sheet-tasks":
                repository.apply_schema()
                sync_task_workbook(client, repository, task_settings)
                print(json.dumps({"status": "success", "operation": "sync-sheet-tasks"}, ensure_ascii=False, indent=2))
                return 0

            worker = Stage1Worker(repository, browser, settings)
            tasks = execute_marked_sheet_tasks(
                client,
                repository,
                worker,
                settings,
                task_settings,
                load_city_catalog(Path(__file__).resolve().parent / "data" / "cities.json"),
                apply=args.apply,
            )
        except (ConfigurationError, SheetTaskError) as error:
            print(json.dumps({"status": "invalid", "error": str(error)}, ensure_ascii=False, indent=2))
            return 2
        except RunHaltedError as error:
            print(json.dumps({
                "status": "halted",
                "run_id": error.run_id,
                "consecutive_browser_errors": error.consecutive_errors,
                "reason": error.reason,
                "artifacts_dir": str(settings.artifacts_dir.resolve()),
            }, ensure_ascii=False, indent=2))
            return 1
        except Exception as error:
            # The planning workbook is commonly shared after deployment. Keep a
            # denied/invalid Google API call actionable instead of exposing a
            # Python traceback to the operator.
            print(json.dumps({"status": "external_error", "error": str(error)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"status": "success" if args.apply else "preview", "tasks": tasks}, ensure_ascii=False, indent=2))
        return 0

    worker = Stage1Worker(repository, browser, settings)
    if args.command == "browser-worker":
        wait_until_stopped(worker)
        return 0
    if args.command == "process-run":
        try:
            processed = worker.process_run(args.run_id)
        except RunHaltedError as error:
            print(json.dumps({
                "status": "halted",
                "run_id": error.run_id,
                "consecutive_browser_errors": error.consecutive_errors,
                "reason": error.reason,
                "artifacts_dir": str(settings.artifacts_dir.resolve()),
            }, ensure_ascii=False))
            return 1
        print(json.dumps({"status": "success", "processed_jobs": processed, "run_id": args.run_id}))
        return 0
    if args.command == "resume-run":
        resumed = repository.resume_run(args.run_id, retry_errors=not args.skip_errors)
        if resumed is None:
            print(json.dumps({"status": "not_found", "run_id": args.run_id}))
            return 1
        try:
            processed = 0 if args.prepare_only else worker.process_run(args.run_id)
        except RunHaltedError as error:
            print(json.dumps({
                "status": "halted",
                "run_id": error.run_id,
                "consecutive_browser_errors": error.consecutive_errors,
                "reason": error.reason,
                "artifacts_dir": str(settings.artifacts_dir.resolve()),
            }, ensure_ascii=False))
            return 1
        print(json.dumps({
            "status": "success",
            "run_id": args.run_id,
            **resumed,
            "processed_jobs": processed,
            "prepared_only": args.prepare_only,
        }))
        return 0
    if args.command == "run-status":
        status = repository.get_run_status(args.run_id)
        if status is None:
            print(json.dumps({"status": "not_found", "run_id": args.run_id}, indent=2))
            return 1
        print(json.dumps({"run_id": args.run_id, **status}, default=str, indent=2))
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
