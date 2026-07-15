"""Google Sheets sync for the Stage 1 operator queue."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

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
