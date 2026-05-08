"""Shared configuration loaders for exporter entrypoints."""

from __future__ import annotations

import os
from pathlib import Path


def _get_env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _get_int_env(name: str, default: int) -> int:
    raw = _get_env(name, str(default))
    try:
        return int(raw or default)
    except Exception as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _build_default_path(export_root: str, *parts: str) -> str:
    return str(Path(export_root).joinpath(*parts))


def load_common_config() -> dict:
    export_root = _get_env("EXPORT_ROOT", "./export")
    export_max_bytes = _get_int_env("EXPORT_MAX_BYTES", 2147483648)
    if export_max_bytes < 0:
        raise ValueError("EXPORT_MAX_BYTES must be a non-negative integer")
    return {
        "export_root": export_root,
        "export_max_bytes": export_max_bytes,
    }


def load_redis_config() -> dict:
    common = load_common_config()
    redis_url = _get_env("REDIS_URL")
    if not redis_url:
        raise ValueError("REDIS_URL is required")

    return {
        **common,
        "redis_url": redis_url,
        "poll_interval": _get_int_env("POLL_INTERVAL_SECONDS", 30),
        "dest_dir": _get_env(
            "DEST_DIR",
            _build_default_path(common["export_root"], "redis", "session_events"),
        ),
        "sidecar_dir": _get_env(
            "REDIS_SIDECARS_DIR",
            _build_default_path(common["export_root"], "redis", "request_sidecars"),
        ),
        "state_path": _get_env(
            "STATE_PATH",
            _build_default_path(common["export_root"], "state", "redis_puller.json"),
        ),
        "missing_skip_seconds": _get_int_env("MISSING_SKIP_SECONDS", 300),
    }


def load_db_config() -> dict:
    common = load_common_config()
    database_url = _get_env("DATABASE_URL") or _get_env("DSN")
    if not database_url:
        db_host = _get_env("DB_HOST")
        db_port = _get_env("DB_PORT", "5432")
        db_user = _get_env("DB_USER")
        db_password = _get_env("DB_PASSWORD")
        db_name = _get_env("DB_NAME")

        if db_host and db_user and db_password and db_name:
            database_url = (
                f"host={db_host} port={db_port} dbname={db_name} "
                f"user={db_user} password={db_password}"
            )

    if not database_url:
        raise ValueError(
            "DATABASE_URL or DSN is required, or set DB_HOST DB_USER DB_PASSWORD DB_NAME"
        )

    db_export_root = _get_env(
        "DB_EXPORT_DIR",
        _build_default_path(common["export_root"], "db"),
    )
    return {
        **common,
        "database_url": database_url,
        "db_export_root": db_export_root,
        "message_request_dir": str(Path(db_export_root) / "message_request"),
        "usage_ledger_dir": str(Path(db_export_root) / "usage_ledger"),
        "state_path": _get_env(
            "DB_STATE_PATH",
            _build_default_path(common["export_root"], "state", "db_exporter.json"),
        ),
        "poll_interval": _get_int_env("DB_POLL_INTERVAL_SECONDS", 300),
        "batch_size": _get_int_env("DB_BATCH_SIZE", 500),
    }


def load_config() -> dict:
    """Backward-compatible alias for the Redis puller."""

    return load_redis_config()
