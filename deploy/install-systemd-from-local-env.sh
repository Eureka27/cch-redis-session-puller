#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_PATH="${1:-${REPO_ROOT}/.env.systemd}"
SERVICE_USER="${SERVICE_USER:-root}"
SERVICE_GROUP="${SERVICE_GROUP:-${SUDO_USER:-${USER:-root}}}"
REDIS_SERVICE_NAME="${REDIS_SERVICE_NAME:-cch-redis-session-puller.service}"
DB_SERVICE_NAME="${DB_SERVICE_NAME:-cch-db-exporter.service}"
REDIS_SERVICE_PATH="/etc/systemd/system/${REDIS_SERVICE_NAME}"
DB_SERVICE_PATH="/etc/systemd/system/${DB_SERVICE_NAME}"

fail() {
  echo "[cch-redis-session-puller][systemd] ERROR: $*" >&2
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

write_service_unit() {
  local path="$1"
  local description="$2"
  local exec_start="$3"

  cat > "${path}" <<UNIT
[Unit]
Description=${description}
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
UMask=0002
EnvironmentFile=${ENV_PATH}
WorkingDirectory=${REPO_ROOT}
ExecStart=${exec_start}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
}

main() {
  require_root
  require_file "${ENV_PATH}"
  require_file "${REPO_ROOT}/deploy/run-puller.sh"
  require_file "${REPO_ROOT}/deploy/run-db-exporter.sh"

  set -a
  # shellcheck disable=SC1090
  source "${ENV_PATH}"
  set +a

  [[ -n "${EXPORT_ROOT:-}" ]] || fail "EXPORT_ROOT is required in ${ENV_PATH}"
  [[ -n "${DEST_DIR:-}" ]] || fail "DEST_DIR is required in ${ENV_PATH}"
  [[ -n "${REDIS_SIDECARS_DIR:-}" ]] || fail "REDIS_SIDECARS_DIR is required in ${ENV_PATH}"
  [[ -n "${STATE_PATH:-}" ]] || fail "STATE_PATH is required in ${ENV_PATH}"
  [[ -n "${DB_EXPORT_DIR:-}" ]] || fail "DB_EXPORT_DIR is required in ${ENV_PATH}"
  [[ -n "${DB_STATE_PATH:-}" ]] || fail "DB_STATE_PATH is required in ${ENV_PATH}"

  install -d -m 0775 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${EXPORT_ROOT}" \
    "${DEST_DIR}" \
    "${REDIS_SIDECARS_DIR}" \
    "$(dirname -- "${STATE_PATH}")" \
    "${DB_EXPORT_DIR}" \
    "$(dirname -- "${DB_STATE_PATH}")"

  chmod 0640 "${ENV_PATH}"
  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${ENV_PATH}"
  chmod +x "${REPO_ROOT}/deploy/run-puller.sh" "${REPO_ROOT}/deploy/run-db-exporter.sh"

  write_service_unit \
    "${REDIS_SERVICE_PATH}" \
    "CCH Redis Session Puller" \
    "${REPO_ROOT}/deploy/run-puller.sh"

  write_service_unit \
    "${DB_SERVICE_PATH}" \
    "CCH DB Exporter" \
    "${REPO_ROOT}/deploy/run-db-exporter.sh"

  systemctl daemon-reload
  systemctl enable --now "${REDIS_SERVICE_NAME}" "${DB_SERVICE_NAME}"
  systemctl --no-pager --full status "${REDIS_SERVICE_NAME}" || true
  systemctl --no-pager --full status "${DB_SERVICE_NAME}" || true
}

main "$@"
