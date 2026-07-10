#!/usr/bin/env sh
set -eu

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example. Review it before running production jobs." >&2
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

mkdir -p "${STAGE1_ARTIFACTS_DIR:-./artifacts/stage1}"
