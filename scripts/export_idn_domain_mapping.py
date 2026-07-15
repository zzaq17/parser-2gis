#!/usr/bin/env python3
"""Export existing Punycode domains from the Stage 1 Google Sheet to TSV."""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from google.oauth2 import service_account
from googleapiclient.discovery import build

from stage1_2gis.domain import canonicalize_domain
from stage1_2gis.google_sheets import SHEET_SCOPES


def _punycode_host(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host if any(label.startswith("xn--") for label in host.split(".")) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spreadsheet-id", default=os.environ.get("STAGE1_SPREADSHEET_ID"))
    parser.add_argument("--credentials-path", default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    parser.add_argument("--input-sheet", default=os.environ.get("STAGE1_INPUT_SHEET", "Ввод"))
    parser.add_argument("--new-domains-sheet", default=os.environ.get("STAGE1_NEW_DOMAINS_SHEET", "NEW domains"))
    parser.add_argument("--output", type=Path, default=Path("results/punycode_domain_mapping.tsv"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.spreadsheet_id:
        raise SystemExit("Missing --spreadsheet-id or STAGE1_SPREADSHEET_ID")
    if not args.credentials_path:
        raise SystemExit("Missing --credentials-path or GOOGLE_APPLICATION_CREDENTIALS")

    credentials = service_account.Credentials.from_service_account_file(args.credentials_path, scopes=SHEET_SCOPES)
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    metadata = service.spreadsheets().get(
        spreadsheetId=args.spreadsheet_id,
        fields="sheets.properties(sheetId,title,gridProperties(rowCount,columnCount))",
    ).execute()
    titles = {sheet["properties"]["title"] for sheet in metadata.get("sheets", [])}
    required_titles = {args.input_sheet, args.new_domains_sheet}
    missing_titles = required_titles - titles
    if missing_titles:
        raise SystemExit(f"Missing sheet tabs: {', '.join(sorted(missing_titles))}")

    sources = (
        (args.input_sheet, "D"),
        (args.input_sheet, "H"),
        (args.new_domains_sheet, "B"),
    )
    ranges = [f"'{sheet}'!{column}3:{column}" for sheet, column in sources]
    response = service.spreadsheets().values().batchGet(
        spreadsheetId=args.spreadsheet_id,
        ranges=ranges,
    ).execute()

    matches: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (sheet, column), value_range in zip(sources, response.get("valueRanges", []), strict=True):
        for row_number, row in enumerate(value_range.get("values", []), start=3):
            punycode = _punycode_host(row[0] if row else "")
            if punycode is None:
                continue
            unicode_domain = canonicalize_domain(punycode)
            if unicode_domain is not None:
                matches[(punycode, unicode_domain)].append(f"{sheet}!{column}{row_number}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file, delimiter="\t", lineterminator="\n")
        writer.writerow(("punycode", "unicode_domain", "occurrence_count", "cells"))
        for (punycode, unicode_domain), cells in sorted(matches.items()):
            writer.writerow((punycode, unicode_domain, len(cells), "; ".join(cells)))

    print(f"Exported {len(matches)} mappings to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
