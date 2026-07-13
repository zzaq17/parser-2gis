#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 QUERIES_TXT [CITIES_LIST_JSON] [--apply]" >&2
  exit 2
fi

queries_file=$1
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cities_list=${2:-"$root/../tasks/cities_list.json"}
apply=${3:-}
if [[ "$cities_list" == "--apply" ]]; then
  apply=$cities_list
  cities_list="$root/../tasks/cities_list.json"
fi
cd "$root"
. ./scripts/stage1-env.sh
python_bin="$root/../.venv/bin/python"
: "${STAGE1_SPREADSHEET_ID:?Set STAGE1_SPREADSHEET_ID in .env}"

"$python_bin" -m stage1_2gis migrate
"$python_bin" -m stage1_2gis sync-google-domains --credentials-path "$GOOGLE_APPLICATION_CREDENTIALS"
job_result=$("$python_bin" scripts/create_jobs_from_cities.py --queries-file "$queries_file" --cities-list "$cities_list")
printf '%s\n' "$job_result"
run_id=$(printf '%s' "$job_result" | "$python_bin" -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
xvfb-run -a --server-args="-screen 0 1280x1024x24 -ac" \
  "$python_bin" -m stage1_2gis process-run --run-id "$run_id"
"$python_bin" -m stage1_2gis run-status --run-id "$run_id"

if [[ "$apply" == "--apply" ]]; then
  "$python_bin" -m stage1_2gis export-ready-candidates --credentials-path "$GOOGLE_APPLICATION_CREDENTIALS" --apply
else
  "$python_bin" -m stage1_2gis export-ready-candidates
fi
