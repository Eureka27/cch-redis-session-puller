#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

REDIS_CONTAINER="${REDIS_CONTAINER:-claude-code-hub-redis}"
DEST_DIR="${DEST_DIR:-${REPO_ROOT}/session}"
STATE_PATH="${STATE_PATH:-${REPO_ROOT}/state/state.json}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-60}"
MISSING_SKIP_SECONDS="${MISSING_SKIP_SECONDS:-300}"

if [[ -z "${REDIS_URL:-}" ]]; then
  REDIS_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${REDIS_CONTAINER}")"
  if [[ -z "${REDIS_IP}" ]]; then
    echo "[cch-redis-session-puller] cannot resolve redis endpoint" >&2
    exit 1
  fi
  export REDIS_URL="redis://${REDIS_IP}:6379/0"
fi

export DEST_DIR
export STATE_PATH
export POLL_INTERVAL_SECONDS
export MISSING_SKIP_SECONDS

PYTHON_BIN="${PYTHON_BIN:-${REPO_ROOT}/.venv/bin/python3}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

exec "${PYTHON_BIN}" "${REPO_ROOT}/src/puller.py"
