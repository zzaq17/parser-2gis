from pathlib import Path

from stage1_2gis.browser import BrowserTimeoutError
from stage1_2gis.config import WorkerSettings
from stage1_2gis.models import UrlJob
from stage1_2gis.worker import RunHaltedError, Stage1Worker


def catalog_document(item_id: str = "123_branch") -> dict:
    return {
        "meta": {"code": 200},
        "result": {
            "items": [
                {
                    "id": item_id,
                    "locale": "ru_RU",
                    "type": "branch",
                    "name": "Тест",
                    "contact_groups": [],
                }
            ]
        },
    }


class FakeRepository:
    def __init__(self):
        self.persisted = []
        self.finished = []
        self.heartbeats = 0

    def persist_item(self, job, document, normalized):
        self.persisted.append((job, document, normalized))

    def finish_job(self, job, **values):
        self.finished.append((job, values))

    def heartbeat(self, job):
        self.heartbeats += 1


class SuccessfulBrowser:
    def collect_items(self, *, on_document, **_kwargs):
        on_document(catalog_document())
        return 1


class FailingBrowser:
    def collect_items(self, **_kwargs):
        raise BrowserTimeoutError("timeout")


def settings(max_attempts: int = 3) -> WorkerSettings:
    return WorkerSettings(
        poll_interval_seconds=1,
        heartbeat_interval_seconds=1,
        stale_job_seconds=10,
        max_attempts=max_attempts,
        browser_timeout_seconds=10,
        artifacts_dir=Path("/tmp/stage1-tests"),
    )


def job(attempt_no: int = 1) -> UrlJob:
    return UrlJob(
        job_id="00000000-0000-0000-0000-000000000001",
        run_id="00000000-0000-0000-0000-000000000002",
        city_key="moscow",
        query_key="test",
        source_url="https://2gis.ru/moscow/search/test",
        max_records=5,
        attempt_no=attempt_no,
        lock_token="00000000-0000-0000-0000-000000000003",
    )


def test_worker_persists_each_item_and_completes():
    repository = FakeRepository()
    worker = Stage1Worker(repository, SuccessfulBrowser(), settings())

    result = worker.process_job(job())

    assert result.status == "completed"
    assert result.items_received == 1
    assert len(repository.persisted) == 1
    assert repository.persisted[0][2].two_gis_item_id == "123_branch"
    assert repository.finished[0][1]["status"] == "completed"


def test_retryable_browser_error_returns_job_to_retry_queue():
    repository = FakeRepository()
    worker = Stage1Worker(repository, FailingBrowser(), settings(max_attempts=3))

    result = worker.process_job(job(attempt_no=1))

    assert result.status == "retry_wait"
    assert result.error_code == "browser_timeout"
    assert repository.finished[0][1]["status"] == "retry_wait"


def test_last_attempt_becomes_failed():
    repository = FakeRepository()
    worker = Stage1Worker(repository, FailingBrowser(), settings(max_attempts=3))

    result = worker.process_job(job(attempt_no=3))

    assert result.status == "failed"
    assert repository.finished[0][1]["status"] == "failed"


class ConsecutiveFailureRepository(FakeRepository):
    def __init__(self):
        super().__init__()
        self.jobs = [job(attempt_no=1), job(attempt_no=2), job(attempt_no=3)]
        self.halted = []

    def claim_job(self, _run_id):
        return self.jobs.pop(0) if self.jobs else None

    def get_run_status(self, _run_id):
        return None

    def halt_run(self, run_id, reason):
        self.halted.append((run_id, reason))


def test_process_run_halts_after_consecutive_browser_errors():
    repository = ConsecutiveFailureRepository()
    worker = Stage1Worker(repository, FailingBrowser(), settings(max_attempts=3))

    try:
        worker.process_run(job().run_id)
    except RunHaltedError as error:
        assert error.consecutive_errors == 3
        assert "browser_timeout" in error.reason
    else:
        raise AssertionError("run should have been halted")

    assert len(repository.finished) == 3
    assert repository.halted[0][0] == job().run_id
