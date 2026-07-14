"""Database-backed Stage 1 browser worker."""

from __future__ import annotations

import logging
import signal
import time
from threading import Event, Thread

from pydantic import ValidationError

from .browser import BrowserAdapter, BrowserError
from .config import WorkerSettings
from .domain import normalize_catalog_document
from .models import UrlJob, WorkerResult
from .persistence import Stage1Repository

LOGGER = logging.getLogger(__name__)


class RunHaltedError(RuntimeError):
    def __init__(self, *, run_id: str, consecutive_errors: int, reason: str) -> None:
        super().__init__(reason)
        self.run_id = run_id
        self.consecutive_errors = consecutive_errors
        self.reason = reason


class Heartbeat:
    def __init__(self, repository: Stage1Repository, job: UrlJob, interval_seconds: int) -> None:
        self.repository = repository
        self.job = job
        self.interval_seconds = interval_seconds
        self._stop_event = Event()
        self._thread = Thread(target=self._run, name=f"heartbeat-{self.job.job_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=self.interval_seconds + 1)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                self.repository.heartbeat(self.job)
            except Exception:
                LOGGER.exception("Heartbeat failed for job %s", self.job.job_id)


class Stage1Worker:
    def __init__(
        self,
        repository: Stage1Repository,
        browser: BrowserAdapter,
        settings: WorkerSettings,
    ) -> None:
        self._repository = repository
        self._browser = browser
        self._settings = settings
        self._stop_event = Event()

    def request_stop(self, *_args) -> None:
        LOGGER.info("Worker shutdown requested")
        self._stop_event.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    def run_forever(self) -> None:
        self.install_signal_handlers()
        LOGGER.info("Stage 1 worker started")
        consecutive_browser_errors = 0
        while not self._stop_event.is_set():
            recovered = self._repository.recover_stale_jobs(self._settings.stale_job_seconds)
            if recovered:
                LOGGER.warning("Recovered %s stale Stage 1 jobs", recovered)
            job = self._repository.claim_job()
            if job is None:
                self._stop_event.wait(self._settings.poll_interval_seconds)
                continue
            result = self.process_job(job)
            consecutive_browser_errors = self._next_browser_error_count(
                consecutive_browser_errors, result
            )
            if consecutive_browser_errors >= self._settings.consecutive_browser_error_limit:
                reason = self._browser_error_halt_reason(consecutive_browser_errors, result)
                self._repository.halt_run(job.run_id, reason)
                LOGGER.error("Stage 1 worker halted: %s", reason)
                self._stop_event.set()
        LOGGER.info("Stage 1 worker stopped")

    def process_run(self, run_id: str) -> int:
        """Process queued jobs from one run and return the number claimed."""
        processed = 0
        consecutive_browser_errors = 0
        while not self._stop_event.is_set():
            job = self._repository.claim_job(run_id)
            if job is None:
                return processed
            result = self.process_job(job)
            processed += 1
            consecutive_browser_errors = self._next_browser_error_count(
                consecutive_browser_errors, result
            )
            if consecutive_browser_errors >= self._settings.consecutive_browser_error_limit:
                reason = self._browser_error_halt_reason(consecutive_browser_errors, result)
                self._repository.halt_run(run_id, reason)
                raise RunHaltedError(
                    run_id=run_id,
                    consecutive_errors=consecutive_browser_errors,
                    reason=reason,
                )
            progress = self._repository.get_run_status(run_id)
            if progress:
                LOGGER.info(
                    "Stage 1 progress run=%s completed=%s/%s failed=%s items=%s",
                    run_id,
                    progress["completed_jobs"],
                    progress["generated_jobs"],
                    progress["failed_jobs"],
                    progress["items_received"],
                )
        return processed

    @staticmethod
    def _next_browser_error_count(current: int, result: WorkerResult) -> int:
        if result.error_code and (
            result.error_code.startswith("browser_") or result.error_code == "captcha_detected"
        ):
            return current + 1
        return 0

    @staticmethod
    def _browser_error_halt_reason(count: int, result: WorkerResult) -> str:
        return (
            f"Stopped after {count} consecutive browser errors. "
            f"Last error: {result.error_code}: {result.error_message}"
        )

    def process_job(self, job: UrlJob) -> WorkerResult:
        heartbeat = Heartbeat(self._repository, job, self._settings.heartbeat_interval_seconds)
        heartbeat.start()
        items_received = 0

        def persist(document: dict) -> None:
            nonlocal items_received
            normalized = normalize_catalog_document(document)
            self._repository.persist_item(job, document, normalized)
            items_received += 1

        try:
            LOGGER.info("Processing Stage 1 job %s: %s", job.job_id, job.source_url)
            self._browser.collect_items(
                url=job.source_url,
                max_records=job.max_records,
                on_document=persist,
                artifact_prefix=f"{job.job_id}-attempt-{job.attempt_no}",
            )
            status = "completed"
            self._repository.finish_job(job, status=status)
            return WorkerResult(job_id=job.job_id, status=status, items_received=items_received)
        except ValidationError as error:
            status = "partial" if items_received else "failed"
            self._repository.finish_job(
                job,
                status=status,
                error_code="catalog_validation_error",
                error_message=str(error),
            )
            return WorkerResult(
                job_id=job.job_id,
                status=status,
                items_received=items_received,
                error_code="catalog_validation_error",
                error_message=str(error),
            )
        except BrowserError as error:
            retryable = error.retryable and job.attempt_no < self._settings.max_attempts
            status = "retry_wait" if retryable else ("partial" if items_received else "failed")
            self._repository.finish_job(
                job,
                status=status,
                error_code=error.error_code,
                error_message=str(error),
            )
            return WorkerResult(
                job_id=job.job_id,
                status=status,
                items_received=items_received,
                error_code=error.error_code,
                error_message=str(error),
            )
        except Exception as error:
            LOGGER.exception("Unexpected Stage 1 job failure: %s", job.job_id)
            retryable = job.attempt_no < self._settings.max_attempts
            status = "retry_wait" if retryable else ("partial" if items_received else "failed")
            self._repository.finish_job(
                job,
                status=status,
                error_code="unexpected_error",
                error_message=str(error),
            )
            return WorkerResult(
                job_id=job.job_id,
                status=status,
                items_received=items_received,
                error_code="unexpected_error",
                error_message=str(error),
            )
        finally:
            heartbeat.stop()


def wait_until_stopped(worker: Stage1Worker) -> None:
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        worker.request_stop()
        time.sleep(0.1)
