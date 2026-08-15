from datetime import UTC, datetime
from uuid import UUID

import pytest

from stage1_2gis.config import SheetTaskSettings, WorkerSettings
from stage1_2gis.sheet_tasks import (
    SheetTaskError,
    create_sheet_task_run,
    marker_selected,
    parse_city_choices,
    parse_phrase_groups,
    result_rows,
    resume_latest_sheet_task_batch,
    summary_rows,
)


def phrase_rows():
    return [
        ["Вертикаль", "Подниша", "Поисковая фраза 2GIS"],
        ["Медицина", "Стоматология", "стоматология"],
        ["Медицина", "Стоматология", "Стоматологическая клиника"],
        ["Медицина", "Офтальмология", "глазная клиника"],
    ]


def test_phrase_groups_preserve_source_rows_and_deduplicate_exact_phrase():
    rows = phrase_rows()
    rows.insert(3, ["Медицина", "Стоматология", "стоматология"])

    groups = parse_phrase_groups(rows)

    assert [(group.vertical, group.subniche, group.phrases, group.source_rows) for group in groups] == [
        ("Медицина", "Офтальмология", ("глазная клиника",), (8,)),
        ("Медицина", "Стоматология", ("стоматология", "Стоматологическая клиника"), (5, 6, 7)),
    ]


def test_phrase_groups_require_the_known_headers_and_complete_rows():
    with pytest.raises(SheetTaskError, match="Expected"):
        parse_phrase_groups([["wrong", "header", "row"]])
    with pytest.raises(SheetTaskError, match="must contain"):
        parse_phrase_groups([phrase_rows()[0], ["Медицина", "", "стоматология"]])


@pytest.mark.parametrize(("value", "expected"), [(True, True), ("TRUE", True), (1, True), ("1", True), (False, False), ("", False), ("0", False)])
def test_marker_selected_accepts_only_checkbox_or_one(value, expected):
    assert marker_selected(value, cell_name="A1") is expected


def test_marker_selected_rejects_an_ambiguous_text_marker():
    with pytest.raises(SheetTaskError, match="checked checkbox or 1"):
        marker_selected("да", cell_name="A1")


def test_city_choices_read_checkbox_and_literal_one():
    choices = parse_city_choices([
        ["Группа", "Город", "В работу"],
        ["million", "Москва", True],
        ["million", "Казань", "1"],
        ["million", "Омск", ""],
        ["", "", False],
    ])

    assert [choice.name for choice in choices if choice.selected] == ["Москва", "Казань"]


class Repository:
    def __init__(self):
        self.runs = []
        self.jobs = []

    def create_run(self, **kwargs):
        self.runs.append(kwargs)

    def create_job(self, **kwargs):
        self.jobs.append(kwargs)


def test_sheet_task_run_snapshots_group_and_creates_phrase_by_city_jobs():
    group = parse_phrase_groups(phrase_rows())[1]
    repository = Repository()
    settings = SheetTaskSettings(spreadsheet_id="planning-sheet")
    worker_settings = WorkerSettings(max_attempts=4)
    catalog = {"Москва": {"name": "Москва", "domain": "ru", "code": "moscow"}}

    run_id, jobs = create_sheet_task_run(
        repository,
        settings,
        worker_settings,
        group,
        ["Москва"],
        catalog,
        run_id="00000000-0000-0000-0000-000000000010",
    )

    assert run_id == "00000000-0000-0000-0000-000000000010"
    assert jobs == 2
    assert repository.runs[0]["task_vertical"] == "Медицина"
    assert repository.runs[0]["snapshot"]["source_rows"] == [5, 6]
    assert repository.runs[0]["snapshot"]["cities"] == ["Москва"]
    assert repository.runs[0]["snapshot"]["cities_by_key"] == {"moscow": "Москва"}
    assert {job["query_key"] for job in repository.jobs} == set(repository.runs[0]["snapshot"]["queries"])
    assert all("2gis.ru/moscow/search/" in job["source_url"] for job in repository.jobs)
    assert repository.runs[0]["sheet_task_batch_id"] is None


class ResumeRepository:
    def __init__(self, runs):
        self.runs = runs
        self.resumed = []

    def get_latest_sheet_task_batch(self):
        return "batch-1", self.runs

    def resume_run(self, run_id, *, retry_errors):
        self.resumed.append((run_id, retry_errors))
        return {"requeued_jobs": 1, "queued_jobs": 1, "completed_jobs": 2, "failed_jobs": 0}


class ResumeWorker:
    def __init__(self):
        self.processed = []

    def process_run(self, run_id):
        self.processed.append(run_id)
        return 1


def test_resume_latest_batch_skips_completed_and_uses_persisted_statuses(monkeypatch):
    repository = ResumeRepository([
        {"run_id": "completed", "status": "completed"},
        {"run_id": "halted", "status": "halted"},
        {"run_id": "queued", "status": "queued"},
    ])
    worker = ResumeWorker()
    syncs = []
    monkeypatch.setattr("stage1_2gis.sheet_tasks.sync_task_workbook", lambda *args: syncs.append(args))

    result = resume_latest_sheet_task_batch(
        object(), repository, worker, SheetTaskSettings(spreadsheet_id="planning-sheet"),
        retry_errors=True, prepare_only=False,
    )

    assert repository.resumed == [("halted", True), ("queued", True)]
    assert worker.processed == ["halted", "queued"]
    assert len(syncs) == 2
    assert result["batch_id"] == "batch-1"
    assert [run["previous_status"] for run in result["runs"]] == ["halted", "queued"]


def test_resume_latest_batch_rejects_completed_batch(monkeypatch):
    monkeypatch.setattr("stage1_2gis.sheet_tasks.sync_task_workbook", lambda *args: None)
    with pytest.raises(SheetTaskError, match="already complete"):
        resume_latest_sheet_task_batch(
            object(), ResumeRepository([{"run_id": "completed", "status": "completed"}]), ResumeWorker(),
            SheetTaskSettings(spreadsheet_id="planning-sheet"), retry_errors=True, prepare_only=False,
        )


def test_summary_uses_latest_run_status_and_pending_checkbox():
    groups = parse_phrase_groups(phrase_rows())
    runs = [{
        "run_id": "run-1",
        "vertical": "Медицина",
        "subniche": "Стоматология",
        "status": "completed_with_errors",
        "generated_jobs": 3,
        "completed_jobs": 2,
        "failed_jobs": 1,
        "companies": 4,
        "domains": 5,
        "started_at": datetime(2026, 8, 10, tzinfo=UTC),
        "finished_at": datetime(2026, 8, 10, 1, tzinfo=UTC),
        "error_summary": "captcha",
        "snapshot": {"cities": ["Москва"]},
    }]

    rows = summary_rows(groups, runs, {("Медицина", "Офтальмология"): True})

    assert rows[0][4] == "Статус"
    assert rows[1][4] == "К запуску"
    assert rows[2][4] == "Готово с ошибками"
    assert rows[2][5] == "run-1"


def test_result_rows_resolve_query_keys_from_the_immutable_snapshot():
    rows = result_rows(
        [{
            "run_id": UUID("00000000-0000-0000-0000-000000000001"), "vertical": "Медицина", "subniche": "Стоматология",
            "domain": "example.ru", "url": "https://example.ru", "company_name": "Клиника",
            "city": "Москва", "rubric": "Стоматология", "is_advertised": True,
            "query_keys": ["query-a"], "city_keys": ["moscow"],
            "found_at": datetime(2026, 8, 10, tzinfo=UTC),
        }],
        [{"run_id": "00000000-0000-0000-0000-000000000001", "snapshot": {"queries": {"query-a": "стоматология"}, "cities_by_key": {"moscow": "Москва"}}}],
    )

    assert rows[1][0] == "00000000-0000-0000-0000-000000000001"
    assert rows[1][3:11] == ["example.ru", "https://example.ru", "Клиника", "Москва", "Стоматология", "Да", "стоматология", "Москва"]
