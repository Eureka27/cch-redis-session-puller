#!/usr/bin/env bash
set -euo pipefail

# ===================== User Config (edit here) =====================
DEPLOY_USER="${SUDO_USER:-${USER:-puller}}"  # systemd User=
EXPORT_ROOT="./export"
CADDY_ENABLE="0"
CADDY_SITE_DOMAIN="apidata.example.com"
CADDY_CONFIG_PATH="/etc/caddy/Caddyfile"

# Redis connection: prefer REDIS_URL if provided, otherwise REDIS_CONTAINER.
REDIS_URL=""
REDIS_CONTAINER="claude-code-hub-redis"
DEST_DIR="./export/redis/session_events"
REDIS_SIDECARS_DIR="./export/redis/request_sidecars"
STATE_PATH="./export/state/redis_puller.json"
POLL_INTERVAL_SECONDS="60"
MISSING_SKIP_SECONDS="300"

# DB connection: set DATABASE_URL or DSN.
DATABASE_URL=""
DSN=""
DB_EXPORT_DIR="./export/db"
DB_STATE_PATH="./export/state/db_exporter.json"
DB_POLL_INTERVAL_SECONDS="300"
DB_BATCH_SIZE="500"
# ================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REDIS_SERVICE_NAME="cch-redis-session-puller.service"
DB_SERVICE_NAME="cch-db-exporter.service"
REDIS_SERVICE_PATH="/etc/systemd/system/${REDIS_SERVICE_NAME}"
DB_SERVICE_PATH="/etc/systemd/system/${DB_SERVICE_NAME}"
SYSTEM_ENV_PATH="/etc/cch-redis-session-puller.env"
LOCAL_ENV_PATH="${REPO_ROOT}/.env.local"

log() {
  echo "[cch-redis-session-puller][deploy] $*"
}

fail() {
  echo "[cch-redis-session-puller][deploy] ERROR: $*" >&2
  exit 1
}

run_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    fail "root or sudo is required for: $*"
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

resolve_path() {
  local p="$1"
  if [[ "${p}" = /* ]]; then
    printf '%s\n' "${p}"
  else
    printf '%s/%s\n' "${REPO_ROOT}" "${p#./}"
  fi
}

validate_config() {
  [[ -n "${DEPLOY_USER}" ]] || fail "DEPLOY_USER cannot be empty"
  [[ -n "${EXPORT_ROOT}" ]] || fail "EXPORT_ROOT cannot be empty"
  [[ -n "${DEST_DIR}" ]] || fail "DEST_DIR cannot be empty"
  [[ -n "${REDIS_SIDECARS_DIR}" ]] || fail "REDIS_SIDECARS_DIR cannot be empty"
  [[ -n "${STATE_PATH}" ]] || fail "STATE_PATH cannot be empty"
  [[ -n "${POLL_INTERVAL_SECONDS}" ]] || fail "POLL_INTERVAL_SECONDS cannot be empty"
  [[ -n "${MISSING_SKIP_SECONDS}" ]] || fail "MISSING_SKIP_SECONDS cannot be empty"
  [[ -n "${DB_EXPORT_DIR}" ]] || fail "DB_EXPORT_DIR cannot be empty"
  [[ -n "${DB_STATE_PATH}" ]] || fail "DB_STATE_PATH cannot be empty"
  [[ -n "${DB_POLL_INTERVAL_SECONDS}" ]] || fail "DB_POLL_INTERVAL_SECONDS cannot be empty"
  [[ -n "${DB_BATCH_SIZE}" ]] || fail "DB_BATCH_SIZE cannot be empty"

  if [[ -z "${REDIS_URL}" && -z "${REDIS_CONTAINER}" ]]; then
    fail "REDIS_URL and REDIS_CONTAINER cannot both be empty"
  fi

  if [[ -z "${DATABASE_URL}" && -z "${DSN}" ]]; then
    fail "DATABASE_URL and DSN cannot both be empty"
  fi

  if [[ "${CADDY_ENABLE}" != "0" && "${CADDY_ENABLE}" != "1" ]]; then
    fail "CADDY_ENABLE must be 0 or 1"
  fi

  if [[ "${CADDY_ENABLE}" == "1" ]]; then
    [[ -n "${CADDY_SITE_DOMAIN}" ]] || fail "CADDY_SITE_DOMAIN cannot be empty when CADDY_ENABLE=1"
    [[ -n "${CADDY_CONFIG_PATH}" ]] || fail "CADDY_CONFIG_PATH cannot be empty when CADDY_ENABLE=1"
    if [[ "${CADDY_CONFIG_PATH}" != /* ]]; then
      fail "CADDY_CONFIG_PATH must be an absolute path"
    fi
  fi
}

write_env_file() {
  mkdir -p \
    "$(resolve_path "${EXPORT_ROOT}")" \
    "$(resolve_path "${DEST_DIR}")" \
    "$(resolve_path "${REDIS_SIDECARS_DIR}")" \
    "$(dirname -- "$(resolve_path "${STATE_PATH}")")" \
    "$(resolve_path "${DB_EXPORT_DIR}")" \
    "$(dirname -- "$(resolve_path "${DB_STATE_PATH}")")"

  cat > "${LOCAL_ENV_PATH}" <<ENV
EXPORT_ROOT=${EXPORT_ROOT}
REDIS_CONTAINER=${REDIS_CONTAINER}
REDIS_URL=${REDIS_URL}
DEST_DIR=${DEST_DIR}
REDIS_SIDECARS_DIR=${REDIS_SIDECARS_DIR}
STATE_PATH=${STATE_PATH}
POLL_INTERVAL_SECONDS=${POLL_INTERVAL_SECONDS}
MISSING_SKIP_SECONDS=${MISSING_SKIP_SECONDS}
DATABASE_URL=${DATABASE_URL}
DSN=${DSN}
DB_EXPORT_DIR=${DB_EXPORT_DIR}
DB_STATE_PATH=${DB_STATE_PATH}
DB_POLL_INTERVAL_SECONDS=${DB_POLL_INTERVAL_SECONDS}
DB_BATCH_SIZE=${DB_BATCH_SIZE}
ENV

  run_root install -m 0644 "${LOCAL_ENV_PATH}" "${SYSTEM_ENV_PATH}"
}

prepare_python() {
  if [[ ! -d "${REPO_ROOT}/.venv" ]]; then
    python3 -m venv "${REPO_ROOT}/.venv"
  fi

  "${REPO_ROOT}/.venv/bin/pip" install --upgrade pip
  "${REPO_ROOT}/.venv/bin/pip" install -r "${REPO_ROOT}/requirements.txt"
}

write_service_unit() {
  local path="$1"
  local description="$2"
  local exec_start="$3"

  cat > "${path}" <<UNIT
[Unit]
Description=${description}
After=network.target

[Service]
Type=simple
User=${DEPLOY_USER}
EnvironmentFile=${SYSTEM_ENV_PATH}
WorkingDirectory=${REPO_ROOT}
ExecStart=${exec_start}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
}

write_caddy_config() {
  local export_root_path
  local tmp_caddy
  export_root_path="$(resolve_path "${EXPORT_ROOT}")"
  tmp_caddy="$(mktemp)"
  trap 'rm -f "${tmp_caddy}"' RETURN

  cat > "${tmp_caddy}" <<CADDY
${CADDY_SITE_DOMAIN} {
    encode zstd gzip
    root * ${export_root_path}
    file_server browse
}
CADDY

  run_root mkdir -p "$(dirname -- "${CADDY_CONFIG_PATH}")"
  run_root install -m 0644 "${tmp_caddy}" "${CADDY_CONFIG_PATH}"
  run_root systemctl reload caddy
  run_root systemctl --no-pager --full status caddy || true
}

install_services() {
  local tmp_redis_unit
  local tmp_db_unit
  tmp_redis_unit="$(mktemp)"
  tmp_db_unit="$(mktemp)"
  trap 'rm -f "${tmp_redis_unit}" "${tmp_db_unit}"' RETURN

  write_service_unit \
    "${tmp_redis_unit}" \
    "CCH Redis Session Puller" \
    "${REPO_ROOT}/deploy/run-puller.sh"
  write_service_unit \
    "${tmp_db_unit}" \
    "CCH DB Exporter" \
    "${REPO_ROOT}/deploy/run-db-exporter.sh"

  run_root install -m 0644 "${tmp_redis_unit}" "${REDIS_SERVICE_PATH}"
  run_root install -m 0644 "${tmp_db_unit}" "${DB_SERVICE_PATH}"
  run_root chmod +x "${REPO_ROOT}/deploy/run-puller.sh"
  run_root chmod +x "${REPO_ROOT}/deploy/run-db-exporter.sh"
  run_root systemctl daemon-reload
  run_root systemctl enable --now "${REDIS_SERVICE_NAME}" "${DB_SERVICE_NAME}"
  run_root systemctl --no-pager --full status "${REDIS_SERVICE_NAME}" || true
  run_root systemctl --no-pager --full status "${DB_SERVICE_NAME}" || true
}

main() {
  validate_config
  require_cmd python3
  require_cmd systemctl

  log "preparing python virtualenv and dependencies"
  prepare_python

  log "writing environment files"
  write_env_file

  log "installing systemd services for redis_puller and db_exporter"
  install_services

  if [[ "${CADDY_ENABLE}" == "1" ]]; then
    log "writing Caddy static site config"
    write_caddy_config
  fi

  log "done"
}

main "$@"
