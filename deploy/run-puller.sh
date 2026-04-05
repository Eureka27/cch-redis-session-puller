#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

REDIS_CONTAINER="${REDIS_CONTAINER:-claude-code-hub-redis}"
EXPORT_ROOT="${EXPORT_ROOT:-${REPO_ROOT}/export}"
DEST_DIR="${DEST_DIR:-${EXPORT_ROOT}/redis/session_events}"
REDIS_SIDECARS_DIR="${REDIS_SIDECARS_DIR:-${EXPORT_ROOT}/redis/request_sidecars}"
STATE_PATH="${STATE_PATH:-${EXPORT_ROOT}/state/redis_puller.json}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-60}"
MISSING_SKIP_SECONDS="${MISSING_SKIP_SECONDS:-300}"

if [[ -z "${REDIS_URL:-}" ]]; then
  if command -v redis-cli >/dev/null 2>&1 && redis-cli -u "redis://127.0.0.1:6379/0" ping >/dev/null 2>&1; then
    export REDIS_URL="redis://127.0.0.1:6379/0"
  else
    REDIS_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${REDIS_CONTAINER}" 2>/dev/null || true)"
    if [[ -z "${REDIS_IP}" ]]; then
      echo "[cch-redis-session-puller] cannot resolve redis endpoint" >&2
      exit 1
    fi
    export REDIS_URL="redis://${REDIS_IP}:6379/0"
  fi
fi

export EXPORT_ROOT
export DEST_DIR
export REDIS_SIDECARS_DIR
export STATE_PATH
export POLL_INTERVAL_SECONDS
export MISSING_SKIP_SECONDS

PYTHON_BIN="${PYTHON_BIN:-${REPO_ROOT}/.venv/bin/python3}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

exec "${PYTHON_BIN}" "${REPO_ROOT}/src/redis_puller.py"
