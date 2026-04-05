#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

EXPORT_ROOT="${EXPORT_ROOT:-${REPO_ROOT}/export}"
DB_EXPORT_DIR="${DB_EXPORT_DIR:-${EXPORT_ROOT}/db}"
DB_STATE_PATH="${DB_STATE_PATH:-${EXPORT_ROOT}/state/db_exporter.json}"
DB_POLL_INTERVAL_SECONDS="${DB_POLL_INTERVAL_SECONDS:-300}"
DB_BATCH_SIZE="${DB_BATCH_SIZE:-500}"

if [[ -z "${DATABASE_URL:-}" && -z "${DSN:-}" ]]; then
  echo "[cch-redis-session-puller] DATABASE_URL or DSN is required" >&2
  exit 1
fi

export EXPORT_ROOT
export DB_EXPORT_DIR
export DB_STATE_PATH
export DB_POLL_INTERVAL_SECONDS
export DB_BATCH_SIZE

PYTHON_BIN="${PYTHON_BIN:-${REPO_ROOT}/.venv/bin/python3}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

exec "${PYTHON_BIN}" "${REPO_ROOT}/src/db_exporter.py"
