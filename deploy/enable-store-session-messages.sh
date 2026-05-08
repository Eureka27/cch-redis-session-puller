#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/claude-code-hub}"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_DIR}/docker-compose.yaml}"
PROJECT_NAME="${PROJECT_NAME:-claude-code-hub}"
APP_SERVICE="${APP_SERVICE:-app}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:40001/api/actions/health}"

log() {
  printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    fail "run this script with sudo"
  fi
}

require_file() {
  local path="$1"
  [[ -f "${path}" ]] || fail "missing file: ${path}"
}

compose() {
  docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" -p "${PROJECT_NAME}" "$@"
}

set_env_value_unique() {
  local key="$1"
  local value="$2"
  local tmp_file
  tmp_file="$(mktemp)"

  awk -F= -v key="${key}" '$1 != key { print }' "${ENV_FILE}" > "${tmp_file}"
  printf '%s=%s\n' "${key}" "${value}" >> "${tmp_file}"
  mv "${tmp_file}" "${ENV_FILE}"
}

wait_http_health() {
  local attempts="${1:-20}"
  local delay="${2:-3}"
  local i

  for ((i=1; i<=attempts; i++)); do
    if curl -fsS --max-time 5 "${HEALTH_URL}" >/dev/null; then
      return 0
    fi
    sleep "${delay}"
  done

  return 1
}

main() {
  require_root
  require_file "${ENV_FILE}"
  require_file "${COMPOSE_FILE}"

  cp -a "${ENV_FILE}" "${ENV_FILE}.bak.$(date -u +%Y%m%d%H%M%S)"

  log "Setting STORE_SESSION_MESSAGES=true in ${ENV_FILE}"
  set_env_value_unique "STORE_SESSION_MESSAGES" "true"

  log "Recreating ${APP_SERVICE} container"
  compose up -d --force-recreate --no-deps "${APP_SERVICE}"

  if ! wait_http_health; then
    fail "health check failed after recreating app"
  fi

  log "claude-code-hub is healthy and STORE_SESSION_MESSAGES=true is applied"
  grep '^STORE_SESSION_MESSAGES=' "${ENV_FILE}" || true
}

main "$@"
