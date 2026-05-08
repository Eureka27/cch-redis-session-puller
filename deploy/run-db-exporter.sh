#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

EXPORT_ROOT="${EXPORT_ROOT:-${REPO_ROOT}/export}"
EXPORT_MAX_BYTES="${EXPORT_MAX_BYTES:-2147483648}"
DB_EXPORT_DIR="${DB_EXPORT_DIR:-${EXPORT_ROOT}/db}"
DB_STATE_PATH="${DB_STATE_PATH:-${EXPORT_ROOT}/state/db_exporter.json}"
DB_POLL_INTERVAL_SECONDS="${DB_POLL_INTERVAL_SECONDS:-300}"
DB_BATCH_SIZE="${DB_BATCH_SIZE:-500}"
DATABASE_CONTAINER="${DATABASE_CONTAINER:-}"
DB_HOST="${DB_HOST:-}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-}"

if [[ -z "${DATABASE_URL:-}" && -z "${DSN:-}" ]]; then
  if [[ -z "${DB_HOST}" && -n "${DATABASE_CONTAINER}" ]]; then
    DB_HOST="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${DATABASE_CONTAINER}" 2>/dev/null || true)"
  fi

  if [[ -z "${DB_HOST}" || -z "${DB_USER}" || -z "${DB_PASSWORD}" || -z "${DB_NAME}" ]]; then
    echo "[cch-redis-session-puller] DATABASE_URL/DSN or DB_HOST/DATABASE_CONTAINER + DB_USER + DB_PASSWORD + DB_NAME is required" >&2
    exit 1
  fi

  export DSN="host=${DB_HOST} port=${DB_PORT} dbname=${DB_NAME} user=${DB_USER} password=${DB_PASSWORD}"
fi

export EXPORT_ROOT
export EXPORT_MAX_BYTES
export DB_EXPORT_DIR
export DB_STATE_PATH
export DB_POLL_INTERVAL_SECONDS
export DB_BATCH_SIZE

PYTHON_BIN="${PYTHON_BIN:-${REPO_ROOT}/.venv/bin/python3}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

exec "${PYTHON_BIN}" "${REPO_ROOT}/src/db_exporter.py"
