# Stage 1 Runbook

`stage1_2gis` collects 2GIS cards, keeps normalized domains in PostgreSQL,
excludes domains already present in Google Sheets, and appends only new
candidates to `NEW domains`.

## One-time setup

1. Install Python 3.12 and Xvfb.
2. Create and populate the virtual environment:

```bash
python3.12 -m venv ../.venv
../.venv/bin/python -m pip install -e '.[dev]'
../.venv/bin/python -m playwright install chromium
```

3. Copy `.env.example` to `.env`. Set all `POSTGRES_*` values, the absolute
`GOOGLE_APPLICATION_CREDENTIALS` path, `STAGE1_SPREADSHEET_ID`, and the exact
Google Sheets tab names. Share the spreadsheet with the service-account email.
4. Put one search phrase per line in `../tasks/queries.txt`. Empty lines and
lines beginning with `#` are ignored. Cities are read from
`../tasks/cities_list.json`.

## Validate configuration

```bash
cd /mnt/f/gsearch/parser-2gis-pydantic2
../.venv/bin/python -m stage1_2gis migrate
../.venv/bin/python -m stage1_2gis sync-google-domains
```

The second command snapshots `Ввод!D`, `Ввод!H`, and `NEW domains!B` in
`stage1_2gis.google_domain_snapshot`. It does not change the sheet.
The migration also canonicalizes legacy `www.example.ru` domain keys to
`example.ru`; stored website URLs remain unchanged.

## Daily pipeline

Preview only; this does not write to Google Sheets:

```bash
bash scripts/run_stage1_pipeline.sh ../tasks/queries.txt
```

After reviewing the JSON preview, append new domains:

```bash
bash scripts/run_stage1_pipeline.sh ../tasks/queries.txt --apply
```

The browser is launched through Xvfb. It runs in normal headed mode but no
window is shown in Windows. The pipeline processes only its own generated
run, not another queued run. Every invocation creates a new `run_id`; therefore
running the pipeline again (including with `queries-2.txt`) starts a new run
and does **not** continue a halted one. Because the script uses `set -e`, its
`--apply` export is not reached if browser processing halts.

## Resume a halted run

Use this procedure to continue the same run without reprocessing completed
jobs. It requeues interrupted/delayed jobs and retries failed or partial jobs;
completed jobs are left unchanged. Stop any older worker for this run before
continuing, so two processes cannot claim the same jobs.

```bash
cd /mnt/f/gsearch/parser-2gis-pydantic2

# Resume, print the resulting status, and preview ready candidates.
bash scripts/run_stage1_pipeline_resume.sh 3a00a577-c270-4afc-8546-be08f06b7cc8

# Same, then append ready candidates to Google Sheets after a successful resume.
bash scripts/run_stage1_pipeline_resume.sh 3a00a577-c270-4afc-8546-be08f06b7cc8 --apply
```

The script loads `.env`, runs the database migration and Google-domain sync,
uses Xvfb, and stops before either preview or export if `resume-run` halts.
Pass the actual run UUID without `<` or `>`.

For a halted browser run, do not invoke `process-run` or `resume-run` directly
while `STAGE1_HEADED=1`: Chromium needs either Xvfb as above or a real, visible
`DISPLAY`. To run headlessly instead, set `STAGE1_HEADED=0` explicitly and use
the same `resume-run` command without `xvfb-run`.

## Progress telemetry

Every URL job updates its persisted status and received-card count. While a
run is active, inspect its progress from another terminal:

```bash
../.venv/bin/python -m stage1_2gis run-status --run-id RUN_ID
watch -n 5 '../.venv/bin/python -m stage1_2gis run-status --run-id RUN_ID'
```

The JSON includes `progress_percent`, queued/running/retry/partial/completed
and failed job counts, received cards, companies, domains, and timestamps.
`process-run` also writes one progress log line after each completed URL job.

## Browser errors and debug artifacts

Each failed browser attempt writes a debug bundle to `STAGE1_ARTIFACTS_DIR`:

- `JOB_ID-attempt-N.png` — full-page screenshot;
- `JOB_ID-attempt-N.html` — page HTML for text and selector inspection;
- `JOB_ID-attempt-N.zip` — Playwright trace, opened with `playwright show-trace`;
- `JOB_ID-attempt-N.json` — requested/current URLs, page title, error and captcha marker.

Captcha-like page text is classified as `captcha_detected`. After three
consecutive browser errors (`STAGE1_CONSECUTIVE_BROWSER_ERROR_LIMIT`) the run
is marked `halted`, browser processing stops, and `process-run`/`resume-run`
returns exit code 1 with the reason and absolute artifacts directory. Queued
jobs remain available for a later `resume-run`.

For manual intervention, inspect the debug bundle and stop the old worker.
Use the resume procedure above after resolving the problem. For a visual
investigation, run Chromium only in a desktop session with a real `DISPLAY`;
otherwise retain `xvfb-run`:

```bash
xvfb-run -a --server-args="-screen 0 1280x1024x24 -ac" \
  ../.venv/bin/python -m stage1_2gis resume-run --run-id RUN_ID
```

Change the consecutive error threshold in `.env` only when the debug evidence
shows transient errors, not a captcha or access block.

To use another city JSON file:

```bash
bash scripts/run_stage1_pipeline.sh ../tasks/queries.txt /absolute/path/cities.json --apply
```

## Individual commands

```bash
# Generate one run from cities x TXT phrases.
../.venv/bin/python scripts/create_jobs_from_cities.py \
  --cities-list ../tasks/cities_list.json \
  --queries-file ../tasks/queries.txt

# Process a returned run id invisibly.
xvfb-run -a --server-args="-screen 0 1280x1024x24 -ac" \
  ../.venv/bin/python -m stage1_2gis process-run --run-id RUN_ID

# Resume an interrupted run, including failed and partial jobs.
# Stop the previous worker before running this command.
xvfb-run -a --server-args="-screen 0 1280x1024x24 -ac" \
  ../.venv/bin/python -m stage1_2gis resume-run --run-id RUN_ID

# Only rebuild the queue; do not launch browser processing.
../.venv/bin/python -m stage1_2gis resume-run \
  --run-id RUN_ID \
  --prepare-only

# Continue queued/interrupted jobs without retrying failed/partial jobs.
xvfb-run -a --server-args="-screen 0 1280x1024x24 -ac" \
  ../.venv/bin/python -m stage1_2gis resume-run \
  --run-id RUN_ID \
  --skip-errors

# Inspect candidates after the Google anti-join.
../.venv/bin/python -m stage1_2gis export-ready-candidates --limit 50

# Append all ready candidates to the configured NEW domains tab.
../.venv/bin/python -m stage1_2gis export-ready-candidates --apply
```

## Result and recovery

`stage1_2gis.ready_candidates` contains one candidate per canonical domain,
ordered by `is_advertised` then domain. Each exported row fills `NEW domains!A:H`:
`url`, `domain`, `name`, `city`, `rubric`, `is_advertised`, `source`, and export
date. The source is `2ГИС реклама` for advertised rows and `2ГИС` otherwise.
Exports are recorded in
`stage1_2gis.google_exports`, so a second `--apply` does not duplicate rows.
IDN keys are canonicalized as Unicode (for example, both `пример.рф` and
`xn--e1afmkfd.xn--p1ai` become `пример.рф`) during parsing, Google snapshot
sync, migration, and export.

For a failed run, inspect `stage1_2gis.url_jobs.error_code` and
`error_message`. Resume all unfinished jobs and give `failed`/`partial` jobs a
fresh attempt budget. Stop the old worker first so it cannot process the same
`running` job concurrently:

```bash
xvfb-run -a --server-args="-screen 0 1280x1024x24 -ac" \
  ../.venv/bin/python -m stage1_2gis resume-run --run-id RUN_ID
```

The command leaves completed jobs unchanged, immediately releases interrupted
`running` jobs, makes delayed retries runnable, and retries failed/partial
jobs. Use `--skip-errors` to process only the unfinished queue, or
`--prepare-only` to rebuild the queue without starting a browser.

If job creation itself was interrupted, rerun `create_jobs_from_cities.py`
with the same `--run-id` and original arguments first. Job creation is
idempotent for a run, so existing jobs are retained and only missing city/query
jobs are added. Then run `resume-run`.
