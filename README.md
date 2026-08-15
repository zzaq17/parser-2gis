# Stage 1 — 2GIS acquisition worker

Server-side 2GIS parser for the contacts pipeline. It runs Playwright-managed
Chromium, persists catalog records and normalized domains in PostgreSQL,
deduplicates candidates against Google Sheets, and exports new domains to the
operator queue.

## Requirements

- Python 3.12 or 3.13
- PostgreSQL
- Chromium installed through Playwright
- Google service-account credentials for Sheets sync and export

## Local setup

```bash
python -m venv ../.venv
../.venv/bin/pip install -e .[dev]
../.venv/bin/python -m playwright install chromium
cp .env.example .env
```

Fill in `.env`, then load it into the current shell:

```bash
. ./scripts/stage1-env.sh
```

## Pipeline

Apply or update the PostgreSQL schema:

```bash
../.venv/bin/python -m stage1_2gis migrate
```

Run the complete batch pipeline:

```bash
./scripts/run_stage1_pipeline.sh QUERIES.txt [CITIES_LIST.json] [--apply]
```

Without `--apply`, the final Google Sheets export is preview-only. The script
creates jobs, processes the run, prints persisted status, and exports only
domains that are absent from the configured `Ввод` and `NEW domains` ranges.

Useful standalone commands:

```bash
../.venv/bin/python -m stage1_2gis sync-google-domains
../.venv/bin/python -m stage1_2gis export-ready-candidates --limit 50
../.venv/bin/python -m stage1_2gis export-ready-candidates --apply
../.venv/bin/python scripts/export_idn_domain_mapping.py
```

## Planning tasks from Google Sheets

Keep the planning workbook separate from the existing downstream queue by
setting `STAGE1_TASKS_SPREADSHEET_ID`. Share that workbook with the same service
account as `GOOGLE_APPLICATION_CREDENTIALS`, then initialize it once:

```bash
../.venv/bin/python -m stage1_2gis init-sheet-tasks --apply
```

This creates `Города 2GIS`, `Сводка 2GIS`, and `Результаты 2GIS`. Mark cities
with a checked checkbox or `1`, then mark one or more subniches in `К запуску`.
Preview the exact runs without writing anything, or create and process them:

```bash
../.venv/bin/python -m stage1_2gis sheet-tasks
xvfb-run -a --server-args="-screen 0 1280x1024x24 -ac" \
  ../.venv/bin/python -m stage1_2gis sheet-tasks --apply
../.venv/bin/python -m stage1_2gis sync-sheet-tasks
```

To resume the last `sheet-tasks --apply` batch without restoring its checkboxes:

```bash
xvfb-run -a --server-args="-screen 0 1280x1024x24 -ac" \
  ../.venv/bin/python -m stage1_2gis resume-sheet-tasks
```

`sync-sheet-tasks` rebuilds the managed summary and result tabs from PostgreSQL
after an interruption. It never writes to `STAGE1_SPREADSHEET_ID` or `NEW domains`.

IDN domains use Unicode as the canonical key: both `пример.рф` and
`xn--e1afmkfd.xn--p1ai` are stored and exported as `пример.рф`. A leading
`www.` is removed from domain keys; original website URLs are preserved.
Known `clck.ru` links are resolved through at most five redirects before the
domain is persisted. The original URL stays in the normalized source payload,
while the final URL/domain mapping is used for candidate export; an unresolved
shortener is not emitted as a business domain.

## Development checks

```bash
../.venv/bin/python -m pytest
../.venv/bin/python -m ruff check stage1_2gis scripts tests
../.venv/bin/python -m mypy stage1_2gis
```

See [RUNBOOK.md](RUNBOOK.md) for operations and recovery procedures and
[deploy/README.md](deploy/README.md) for Debian 13 deployment.

## Repository layout

```text
stage1_2gis/   application package, PostgreSQL persistence, and schema
scripts/       batch pipeline and operator utilities
tests/         Stage 1 automated tests
deploy/        systemd and Debian deployment files
results/       local operator exports; intentionally ignored by Git
```
