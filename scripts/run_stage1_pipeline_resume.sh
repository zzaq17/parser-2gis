#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "$2" != "--apply" ) ]]; then
  echo "Usage: $0 RUN_ID [--apply]" >&2
  exit 2
fi

run_id=$1
apply=${2:-}
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
. ./scripts/stage1-env.sh
python_bin="$root/../.venv/bin/python"
: "${STAGE1_SPREADSHEET_ID:?Set STAGE1_SPREADSHEET_ID in .env}"

# Keep the domain anti-join current, just as the new-run pipeline does.
"$python_bin" -m stage1_2gis migrate
"$python_bin" -m stage1_2gis sync-google-domains --credentials-path "$GOOGLE_APPLICATION_CREDENTIALS"

# STAGE1_HEADED=1 is the default and needs an X server in WSL/SSH sessions.
xvfb-run -a --server-args="-screen 0 1280x1024x24 -ac" \
  "$python_bin" -m stage1_2gis resume-run --run-id "$run_id"
"$python_bin" -m stage1_2gis run-status --run-id "$run_id"

if [[ "$apply" == "--apply" ]]; then
  "$python_bin" -m stage1_2gis export-ready-candidates --credentials-path "$GOOGLE_APPLICATION_CREDENTIALS" --apply
else
  "$python_bin" -m stage1_2gis export-ready-candidates
fi
