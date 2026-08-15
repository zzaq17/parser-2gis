"""Planning-workbook parsing and task-run orchestration for Stage 1."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from .config import SheetTaskSettings, WorkerSettings
from .persistence import Stage1Repository
from .worker import RunHaltedError, Stage1Worker

PHRASE_HEADERS = ("Вертикаль", "Подниша", "Поисковая фраза 2GIS")
SUMMARY_HEADERS = (
    "Вертикаль",
    "Подниша",
    "Фраз",
    "К запуску",
    "Статус",
    "run_id",
    "Городов",
    "URL-заданий",
    "Выполнено",
    "Ошибок",
    "Компаний",
    "Доменов",
    "Начато",
    "Завершено",
    "Ошибка",
)
RESULT_HEADERS = (
    "run_id",
    "Вертикаль",
    "Подниша",
    "Домен",
    "URL",
    "Компания",
    "Город 2GIS",
    "Рубрика 2GIS",
    "Реклама 2GIS",
    "Поисковые фразы",
    "Города задания",
    "Найдено",
)
CITIES_HEADERS = ("Группа", "Город", "В работу")


class SheetTaskError(ValueError):
    """Raised when an operator-controlled planning sheet is invalid."""


@dataclass(frozen=True, slots=True)
class PhraseGroup:
    vertical: str
    subniche: str
    phrases: tuple[str, ...]
    source_rows: tuple[int, ...]

    @property
    def key(self) -> tuple[str, str]:
        return self.vertical, self.subniche


@dataclass(frozen=True, slots=True)
class CityChoice:
    group: str
    name: str
    selected: bool


class PlanningSheetClient(Protocol):
    def read_phrase_rows(self, settings: SheetTaskSettings) -> list[list[object]]: ...
    def read_cities(self, settings: SheetTaskSettings) -> list[list[object]]: ...
    def read_summary_controls(self, settings: SheetTaskSettings) -> list[list[object]]: ...
    def initialize_task_sheets(self, settings: SheetTaskSettings, cities: list[tuple[str, str]]) -> None: ...
    def write_task_summary(self, settings: SheetTaskSettings, rows: list[list[object]]) -> None: ...
    def write_task_results(self, settings: SheetTaskSettings, rows: list[list[object]]) -> None: ...


def _cell(value: object) -> str:
    return str(value).strip() if value is not None else ""


def marker_selected(value: object, *, cell_name: str) -> bool:
    """Accept a checked native checkbox or literal 1, and reject ambiguity."""
    if value is True or value == 1:
        return True
    normalized = _cell(value).casefold()
    if normalized in {"", "false", "0"}:
        return False
    if normalized in {"true", "1"}:
        return True
    raise SheetTaskError(f"{cell_name}: expected a checked checkbox or 1, got {_cell(value)!r}")


def parse_phrase_groups(rows: list[list[object]], *, header_row_number: int = 4) -> list[PhraseGroup]:
    if not rows or tuple(_cell(cell) for cell in rows[0][:3]) != PHRASE_HEADERS:
        raise SheetTaskError(
            f"Expected {PHRASE_HEADERS!r} in row {header_row_number} of the phrases sheet"
        )
    grouped: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for offset, row in enumerate(rows[1:], start=header_row_number + 1):
        vertical, subniche, phrase = (_cell(row[index]) if index < len(row) else "" for index in range(3))
        if not any((vertical, subniche, phrase)):
            continue
        if not all((vertical, subniche, phrase)):
            raise SheetTaskError(f"Phrase row {offset} must contain vertical, subniche, and phrase")
        grouped[(vertical, subniche)].append((phrase, offset))

    groups: list[PhraseGroup] = []
    for (vertical, subniche), values in sorted(grouped.items()):
        seen: set[str] = set()
        unique: list[tuple[str, int]] = []
        for phrase, row_number in values:
            phrase_key = phrase.casefold()
            if phrase_key in seen:
                continue
            seen.add(phrase_key)
            unique.append((phrase, row_number))
        groups.append(
            PhraseGroup(
                vertical=vertical,
                subniche=subniche,
                phrases=tuple(phrase for phrase, _ in unique),
                source_rows=tuple(row_number for _, row_number in values),
            )
        )
    if not groups:
        raise SheetTaskError("The phrases sheet has no complete phrase rows")
    return groups


def parse_city_choices(rows: list[list[object]]) -> list[CityChoice]:
    if not rows or tuple(_cell(cell) for cell in rows[0][:3]) != CITIES_HEADERS:
        raise SheetTaskError(f"Expected {CITIES_HEADERS!r} in row 1 of the cities sheet")
    choices: list[CityChoice] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows[1:], start=2):
        group, name = (_cell(row[index]) if index < len(row) else "" for index in range(2))
        marker = row[2] if len(row) > 2 else ""
        selected = marker_selected(marker, cell_name=f"City row {row_number}")
        # Checkbox validation makes an otherwise empty row read back as FALSE.
        # It is still an unused row, not malformed operator input.
        if not group and not name and not selected:
            continue
        if not group or not name:
            raise SheetTaskError(f"City row {row_number} must contain group and city")
        if name.casefold() in seen:
            raise SheetTaskError(f"City row {row_number} duplicates city {name!r}")
        seen.add(name.casefold())
        choices.append(CityChoice(group=group, name=name, selected=selected))
    if not choices:
        raise SheetTaskError("The cities sheet has no city rows; run init-sheet-tasks first")
    return choices


def parse_summary_controls(rows: list[list[object]], groups: Iterable[PhraseGroup]) -> dict[tuple[str, str], bool]:
    known_keys = {group.key for group in groups}
    controls: dict[tuple[str, str], bool] = {}
    for row_number, row in enumerate(rows, start=2):
        vertical, subniche = (_cell(row[index]) if index < len(row) else "" for index in range(2))
        marker = row[3] if len(row) > 3 else ""
        if not vertical and not subniche:
            continue
        key = (vertical, subniche)
        if key not in known_keys:
            raise SheetTaskError(f"Summary row {row_number} refers to unknown niche {vertical!r} / {subniche!r}")
        if key in controls:
            raise SheetTaskError(f"Summary row {row_number} duplicates niche {vertical!r} / {subniche!r}")
        controls[key] = marker_selected(marker, cell_name=f"Summary row {row_number}")
    return controls


def task_status(run: dict[str, Any] | None, *, launch_requested: bool) -> str:
    if launch_requested:
        return "К запуску"
    if run is None:
        return "Не запланировано"
    return {
        "queued": "Ожидает",
        "running": "В работе",
        "completed": "Готово",
        "completed_with_errors": "Готово с ошибками",
        "halted": "Остановлено",
    }.get(str(run["status"]), str(run["status"]))


def _as_iso(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else _cell(value)


def latest_runs_by_group(runs: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs:
        vertical, subniche = _cell(run.get("vertical")), _cell(run.get("subniche"))
        if vertical and subniche and (vertical, subniche) not in latest:
            latest[(vertical, subniche)] = run
    return latest


def snapshot_cities(run: dict[str, Any] | None) -> list[str]:
    if run is None:
        return []
    snapshot = run.get("snapshot")
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    if not isinstance(snapshot, dict):
        return []
    return [str(city) for city in snapshot.get("cities", [])]


def summary_rows(
    groups: Iterable[PhraseGroup],
    runs: Iterable[dict[str, Any]],
    controls: dict[tuple[str, str], bool],
) -> list[list[object]]:
    latest = latest_runs_by_group(runs)
    rows: list[list[object]] = [list(SUMMARY_HEADERS)]
    for group in groups:
        run = latest.get(group.key)
        rows.append([
            group.vertical,
            group.subniche,
            len(group.phrases),
            controls.get(group.key, False),
            task_status(run, launch_requested=controls.get(group.key, False)),
            _cell(run.get("run_id")) if run else "",
            len(snapshot_cities(run)),
            run.get("generated_jobs", "") if run else "",
            run.get("completed_jobs", "") if run else "",
            run.get("failed_jobs", "") if run else "",
            run.get("companies", "") if run else "",
            run.get("domains", "") if run else "",
            _as_iso(run.get("started_at")) if run else "",
            _as_iso(run.get("finished_at")) if run else "",
            _cell(run.get("error_summary")) if run else "",
        ])
    return rows


def _query_key(phrase: str) -> str:
    return f"query-{hashlib.sha256(phrase.casefold().encode('utf-8')).hexdigest()[:12]}"


def _build_url(city: dict[str, str], phrase: str) -> str:
    return f'https://2gis.{city["domain"]}/{city["code"]}/search/{quote(phrase)}/filters/sort=name'


def load_city_catalog(path: Path) -> dict[str, dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["name"]: item for item in data}


def load_city_groups(path: Path) -> list[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return [(group, city) for group, cities in data.items() for city in cities]


def create_sheet_task_run(
    repository: Stage1Repository,
    settings: SheetTaskSettings,
    worker_settings: WorkerSettings,
    group: PhraseGroup,
    cities: list[str],
    city_catalog: dict[str, dict[str, str]],
    *,
    run_id: str | None = None,
    batch_id: str | None = None,
) -> tuple[str, int]:
    missing = [name for name in cities if name not in city_catalog]
    if missing:
        raise SheetTaskError("Cities are absent from the 2GIS catalog: " + ", ".join(missing))
    run_id = run_id or str(uuid.uuid4())
    queries = {_query_key(phrase): phrase for phrase in group.phrases}
    snapshot = {
        "planning_spreadsheet_id": settings.spreadsheet_id,
        "phrases_sheet": settings.phrases_sheet,
        "source_rows": list(group.source_rows),
        "vertical": group.vertical,
        "subniche": group.subniche,
        "queries": queries,
        "cities": cities,
        "cities_by_key": {city_catalog[city]["code"]: city for city in cities},
        "sheet_task_batch_id": batch_id,
    }
    repository.create_run(
        run_id=run_id,
        command_id="sheet-tasks",
        snapshot=snapshot,
        task_vertical=group.vertical,
        task_subniche=group.subniche,
        sheet_task_batch_id=batch_id,
    )
    job_count = 0
    for city_name in cities:
        city = city_catalog[city_name]
        for query_key, phrase in queries.items():
            repository.create_job(
                job_id=str(uuid.uuid4()),
                run_id=run_id,
                city_key=city["code"],
                query_key=query_key,
                source_url=_build_url(city, phrase),
                max_records=100,
                max_attempts=worker_settings.max_attempts,
            )
            job_count += 1
    return run_id, job_count


def result_rows(results: Iterable[dict[str, Any]], runs: Iterable[dict[str, Any]]) -> list[list[object]]:
    snapshots = {str(run["run_id"]): run.get("snapshot") for run in runs}
    rows: list[list[object]] = [list(RESULT_HEADERS)]
    for result in results:
        snapshot = snapshots.get(str(result["run_id"]), {})
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        queries = snapshot.get("queries", {}) if isinstance(snapshot, dict) else {}
        cities_by_key = snapshot.get("cities_by_key", {}) if isinstance(snapshot, dict) else {}
        phrases = [queries.get(key, key) for key in result.get("query_keys", [])]
        city_names = [cities_by_key.get(key, key) for key in result.get("city_keys", [])]
        rows.append([
            str(result["run_id"]),
            result["vertical"],
            result["subniche"],
            result["domain"],
            result["url"],
            result["company_name"] or "",
            result["city"] or "",
            result["rubric"] or "",
            "Да" if result["is_advertised"] else "Нет",
            ", ".join(phrases),
            ", ".join(city_names),
            _as_iso(result["found_at"]),
        ])
    return rows


def sync_task_workbook(
    client: PlanningSheetClient,
    repository: Stage1Repository,
    settings: SheetTaskSettings,
    *,
    controls_override: dict[tuple[str, str], bool] | None = None,
) -> None:
    groups = parse_phrase_groups(client.read_phrase_rows(settings))
    controls = parse_summary_controls(client.read_summary_controls(settings), groups)
    if controls_override:
        controls.update(controls_override)
    runs = repository.list_sheet_task_runs()
    client.write_task_summary(settings, summary_rows(groups, runs, controls))
    client.write_task_results(settings, result_rows(repository.list_sheet_task_results(), runs))


def execute_marked_sheet_tasks(
    client: PlanningSheetClient,
    repository: Stage1Repository,
    worker: Stage1Worker,
    worker_settings: WorkerSettings,
    settings: SheetTaskSettings,
    city_catalog: dict[str, dict[str, str]],
    *,
    apply: bool,
) -> list[dict[str, Any]]:
    groups = parse_phrase_groups(client.read_phrase_rows(settings))
    controls = parse_summary_controls(client.read_summary_controls(settings), groups)
    selected_groups = [group for group in groups if controls.get(group.key, False)]
    cities = [choice.name for choice in parse_city_choices(client.read_cities(settings)) if choice.selected]
    if not selected_groups:
        raise SheetTaskError("Mark at least one subniche in the summary sheet")
    if not cities:
        raise SheetTaskError("Mark at least one city in the cities sheet")
    planned = [
        {
            "vertical": group.vertical,
            "subniche": group.subniche,
            "phrases": list(group.phrases),
            "cities": cities,
            "job_count": len(group.phrases) * len(cities),
        }
        for group in selected_groups
    ]
    if not apply:
        return planned

    repository.apply_schema()
    batch_id = str(uuid.uuid4())
    created: list[dict[str, Any]] = []
    cleared_controls = {group.key: False for group in selected_groups}
    for group in selected_groups:
        run_id, job_count = create_sheet_task_run(
            repository, settings, worker_settings, group, cities, city_catalog, batch_id=batch_id,
        )
        created.append({
            "vertical": group.vertical,
            "subniche": group.subniche,
            "run_id": run_id,
            "job_count": job_count,
            "batch_id": batch_id,
        })
    sync_task_workbook(client, repository, settings, controls_override=cleared_controls)

    for task in created:
        try:
            worker.process_run(task["run_id"])
        except RunHaltedError:
            sync_task_workbook(client, repository, settings, controls_override=cleared_controls)
            raise
        sync_task_workbook(client, repository, settings, controls_override=cleared_controls)
    return created


def resume_latest_sheet_task_batch(
    client: PlanningSheetClient,
    repository: Stage1Repository,
    worker: Stage1Worker,
    settings: SheetTaskSettings,
    *,
    retry_errors: bool,
    prepare_only: bool,
) -> dict[str, Any]:
    """Continue every unfinished run from the most recent sheet-tasks launch."""
    batch = repository.get_latest_sheet_task_batch()
    if batch is None:
        raise SheetTaskError("No resumable sheet-tasks batch exists")

    batch_id, runs = batch
    resumed: list[dict[str, Any]] = []
    for run in runs:
        if run["status"] == "completed":
            continue
        if run["status"] not in {"queued", "running", "halted", "completed_with_errors"}:
            raise SheetTaskError(
                f"Latest sheet-tasks batch contains unsupported run status {run['status']!r}"
            )
        run_id = str(run["run_id"])
        recovery = repository.resume_run(run_id, retry_errors=retry_errors)
        if recovery is None:
            raise SheetTaskError(f"Sheet task run disappeared during resume: {run_id}")
        processed = 0
        try:
            if not prepare_only:
                processed = worker.process_run(run_id)
        except RunHaltedError:
            sync_task_workbook(client, repository, settings)
            raise
        sync_task_workbook(client, repository, settings)
        resumed.append({"run_id": run_id, "previous_status": run["status"], **recovery, "processed_jobs": processed})

    if not resumed:
        raise SheetTaskError("Latest sheet-tasks batch is already complete")
    return {"batch_id": batch_id, "runs": resumed, "prepared_only": prepare_only}
