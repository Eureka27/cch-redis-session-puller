"""Shared JSONL output helpers."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from export_state import ensure_dir
from session_events import sanitize_path_segment


def normalize_json_value(value):
    if isinstance(value, dict):
        return {str(k): normalize_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat() + "Z"
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.hex()
    return value


def append_jsonl(path: Path, records: list[dict]) -> None:
    if not records:
        return
    ensure_dir(str(path.parent))
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(
                json.dumps(
                    normalize_json_value(record),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            f.write("\n")


def build_session_file_path(base_dir: str, session_id: str, suffix: str = ".json") -> Path:
    safe_id = sanitize_path_segment(session_id)
    return Path(base_dir) / f"{safe_id}{suffix}"


def build_daily_jsonl_path(base_dir: str, dt_value, fallback_name: str = "unknown") -> Path:
    if isinstance(dt_value, str):
        day = dt_value[:10] if len(dt_value) >= 10 else fallback_name
    elif isinstance(dt_value, datetime):
        day = dt_value.date().isoformat()
    elif isinstance(dt_value, date):
        day = dt_value.isoformat()
    else:
        day = fallback_name
    return Path(base_dir) / f"{day}.jsonl"
