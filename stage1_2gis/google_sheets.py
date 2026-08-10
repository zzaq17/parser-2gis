"""Google Sheets sync for the Stage 1 operator queue."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from .config import SheetTaskSettings
from .domain import canonicalize_domain

SHEET_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)


def export_source(is_advertised: object) -> str:
    """Return the fixed Stage 1 source label for a candidate."""
    return "2ГИС реклама" if is_advertised else "2ГИС"


def normalize_google_domain(value: object) -> str | None:
    return canonicalize_domain(value)


class GoogleSheetsQueueClient:
    def __init__(self, service: Any) -> None:
        self._service = service

    @classmethod
    def from_service_account(cls, credentials_path: str) -> GoogleSheetsQueueClient:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(credentials_path, scopes=SHEET_SCOPES)
        return cls(build("sheets", "v4", credentials=credentials, cache_discovery=False))

    def read_snapshot_rows(self, spreadsheet_id: str, *, input_sheet: str, new_domains_sheet: str) -> list[tuple[str, str, int | None]]:
        sources = (("input_auto", input_sheet, "D"), ("input_analytics", input_sheet, "H"), ("new_domains", new_domains_sheet, "B"))
        ranges = [f"'{sheet}'!{column}3:{column}" for _, sheet, column in sources]
        response = self._service.spreadsheets().values().batchGet(spreadsheetId=spreadsheet_id, ranges=ranges).execute()
        rows: list[tuple[str, str, int | None]] = []
        for (source_key, _, _), value_range in zip(sources, response.get("valueRanges", []), strict=True):
            for offset, row in enumerate(value_range.get("values", []), start=3):
                domain = normalize_google_domain(row[0] if row else "")
                if domain:
                    rows.append((source_key, domain, offset))
        return rows

    def append_candidates(self, spreadsheet_id: str, *, new_domains_sheet: str, rows: Iterable[dict[str, Any]]) -> int:
        values: list[dict[str, Any]] = []
        seen_domains: set[str] = set()
        for row in rows:
            domain = normalize_google_domain(row.get("domain"))
            if domain is None or domain in seen_domains:
                continue
            seen_domains.add(domain)
            values.append({**row, "domain": domain})
        sheet = self._service.spreadsheets()
        if not values:
            return 0
        exported_on = date.today().isoformat()
        sheet.values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{new_domains_sheet}'!A3:H",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={
                "values": [
                    [
                        *(row[key] or "" for key in ("url", "domain", "name", "city", "rubric")),
                        int(bool(row["is_advertised"])),
                        export_source(row["is_advertised"]),
                        exported_on,
                    ]
                    for row in values
                ]
            },
        ).execute()
        return len(values)

    @staticmethod
    def _range(sheet: str, cell_range: str) -> str:
        return f"'{sheet}'!{cell_range}"

    def _values(self, spreadsheet_id: str, sheet: str, cell_range: str) -> list[list[object]]:
        response = self._service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet, cell_range),
        ).execute()
        return response.get("values", [])

    def _write_values(self, spreadsheet_id: str, sheet: str, cell_range: str, values: list[list[object]]) -> None:
        self._service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet, cell_range),
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()

    def _clear_values(self, spreadsheet_id: str, sheet: str, cell_range: str) -> None:
        self._service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet, cell_range),
            body={},
        ).execute()

    def _sheet_ids(self, spreadsheet_id: str) -> dict[str, int]:
        response = self._service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title,gridProperties))",
        ).execute()
        return {
            sheet["properties"]["title"]: sheet["properties"]["sheetId"]
            for sheet in response.get("sheets", [])
        }

    def _initialize_headers(
        self,
        spreadsheet_id: str,
        sheet: str,
        headers: tuple[str, ...],
        rows: list[list[object]] | None = None,
    ) -> None:
        existing = self._values(spreadsheet_id, sheet, f"A1:{chr(64 + len(headers))}2")
        if existing and any(cell for row in existing for cell in row):
            if tuple(str(cell).strip() for cell in existing[0][:len(headers)]) != headers:
                raise ValueError(f"Sheet {sheet!r} has unexpected headers; refusing to overwrite it")
            return
        self._write_values(spreadsheet_id, sheet, f"A1:{chr(64 + len(headers))}{1 + len(rows or [])}", [list(headers), *(rows or [])])

    def initialize_task_sheets(self, settings: SheetTaskSettings, cities: list[tuple[str, str]]) -> None:
        """Create only the three managed tabs and seed their exact headers."""
        sheets = self._service.spreadsheets()
        existing = self._sheet_ids(settings.spreadsheet_id)
        requested = (settings.cities_sheet, settings.summary_sheet, settings.results_sheet)
        missing = [name for name in requested if name not in existing]
        if missing:
            sheets.batchUpdate(
                spreadsheetId=settings.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": name}}} for name in missing]},
            ).execute()
            existing = self._sheet_ids(settings.spreadsheet_id)

        self._initialize_headers(
            settings.spreadsheet_id,
            settings.cities_sheet,
            ("Группа", "Город", "В работу"),
            [[group, city, False] for group, city in cities],
        )
        self._initialize_headers(
            settings.spreadsheet_id,
            settings.summary_sheet,
            (
                "Вертикаль", "Подниша", "Фраз", "К запуску", "Статус", "run_id", "Городов",
                "URL-заданий", "Выполнено", "Ошибок", "Компаний", "Доменов", "Начато", "Завершено", "Ошибка",
            ),
        )
        self._initialize_headers(
            settings.spreadsheet_id,
            settings.results_sheet,
            (
                "run_id", "Вертикаль", "Подниша", "Домен", "URL", "Компания", "Город 2GIS",
                "Рубрика 2GIS", "Реклама 2GIS", "Поисковые фразы", "Города задания", "Найдено",
            ),
        )

        header_color = {"red": 0.12, "green": 0.47, "blue": 0.71}
        requests: list[dict[str, Any]] = []
        for sheet_name, width in ((settings.cities_sheet, 3), (settings.summary_sheet, 15), (settings.results_sheet, 12)):
            sheet_id = existing[sheet_name]
            requests.extend((
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"backgroundColorStyle": {"rgbColor": header_color}, "textFormat": {"bold": True, "foregroundColorStyle": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}}}},
                        "fields": "userEnteredFormat(backgroundColorStyle,textFormat)",
                    }
                },
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                        "fields": "gridProperties.frozenRowCount",
                    }
                },
                {
                    "autoResizeDimensions": {
                        "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": width},
                    }
                },
            ))
        requests.extend((
            {
                "setDataValidation": {
                    "range": {"sheetId": existing[settings.cities_sheet], "startRowIndex": 1, "endRowIndex": 501, "startColumnIndex": 2, "endColumnIndex": 3},
                    "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True},
                }
            },
            {
                "setDataValidation": {
                    "range": {"sheetId": existing[settings.summary_sheet], "startRowIndex": 1, "endRowIndex": 1001, "startColumnIndex": 3, "endColumnIndex": 4},
                    "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True},
                }
            },
        ))
        sheets.batchUpdate(spreadsheetId=settings.spreadsheet_id, body={"requests": requests}).execute()

    def read_phrase_rows(self, settings: SheetTaskSettings) -> list[list[object]]:
        return self._values(settings.spreadsheet_id, settings.phrases_sheet, "A4:C1000")

    def read_cities(self, settings: SheetTaskSettings) -> list[list[object]]:
        return self._values(settings.spreadsheet_id, settings.cities_sheet, "A1:C500")

    def read_summary_controls(self, settings: SheetTaskSettings) -> list[list[object]]:
        return self._values(settings.spreadsheet_id, settings.summary_sheet, "A2:D1000")

    def write_task_summary(self, settings: SheetTaskSettings, rows: list[list[object]]) -> None:
        self._clear_values(settings.spreadsheet_id, settings.summary_sheet, "A:O")
        self._write_values(settings.spreadsheet_id, settings.summary_sheet, f"A1:O{len(rows)}", rows)

    def write_task_results(self, settings: SheetTaskSettings, rows: list[list[object]]) -> None:
        self._clear_values(settings.spreadsheet_id, settings.results_sheet, "A:L")
        self._write_values(settings.spreadsheet_id, settings.results_sheet, f"A1:L{len(rows)}", rows)
