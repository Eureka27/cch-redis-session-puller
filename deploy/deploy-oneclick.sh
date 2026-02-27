#!/usr/bin/env bash
set -euo pipefail

# ===================== User Config (edit here) =====================
DEPLOY_USER="ubuntu"  # systemd User=

# Redis connection: prefer REDIS_URL if provided, otherwise REDIS_CONTAINER.
REDIS_URL=""
REDIS_CONTAINER="claude-code-hub-redis"

DEST_DIR="./session"
STATE_PATH="./state/state.json"
POLL_INTERVAL_SECONDS="60"
MISSING_SKIP_SECONDS="300"
# ================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SERVICE_NAME="cch-redis-session-puller.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
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
  [[ -n "${DEST_DIR}" ]] || fail "DEST_DIR cannot be empty"
  [[ -n "${STATE_PATH}" ]] || fail "STATE_PATH cannot be empty"
  [[ -n "${POLL_INTERVAL_SECONDS}" ]] || fail "POLL_INTERVAL_SECONDS cannot be empty"
  [[ -n "${MISSING_SKIP_SECONDS}" ]] || fail "MISSING_SKIP_SECONDS cannot be empty"

  if [[ -z "${REDIS_URL}" && -z "${REDIS_CONTAINER}" ]]; then
    fail "REDIS_URL and REDIS_CONTAINER cannot both be empty"
  fi
}

write_env_file() {
  mkdir -p "$(dirname -- "$(resolve_path "${DEST_DIR}")")" "$(dirname -- "$(resolve_path "${STATE_PATH}")")"

  cat > "${LOCAL_ENV_PATH}" <<ENV
REDIS_CONTAINER=${REDIS_CONTAINER}
REDIS_URL=${REDIS_URL}
DEST_DIR=${DEST_DIR}
STATE_PATH=${STATE_PATH}
POLL_INTERVAL_SECONDS=${POLL_INTERVAL_SECONDS}
MISSING_SKIP_SECONDS=${MISSING_SKIP_SECONDS}
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

install_service() {
  local tmp_unit
  tmp_unit="$(mktemp)"
  trap 'rm -f "${tmp_unit}"' RETURN

  cat > "${tmp_unit}" <<UNIT
[Unit]
Description=CCH Redis Session Puller
After=network.target

[Service]
Type=simple
User=${DEPLOY_USER}
EnvironmentFile=${SYSTEM_ENV_PATH}
WorkingDirectory=${REPO_ROOT}
ExecStart=${REPO_ROOT}/deploy/run-puller.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

  run_root install -m 0644 "${tmp_unit}" "${SERVICE_PATH}"
  run_root chmod +x "${REPO_ROOT}/deploy/run-puller.sh"
  run_root systemctl daemon-reload
  run_root systemctl enable --now "${SERVICE_NAME}"
  run_root systemctl --no-pager --full status "${SERVICE_NAME}" || true
}

main() {
  validate_config
  require_cmd python3
  require_cmd systemctl

  if [[ -z "${REDIS_URL}" ]]; then
    require_cmd docker
  fi

  log "preparing python virtualenv and dependencies"
  prepare_python

  log "writing environment files"
  write_env_file

  log "installing systemd service"
  install_service

  log "done"
}

main "$@"
