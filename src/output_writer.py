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


def render_jsonl_payload(records: list[dict]) -> bytes:
    if not records:
        return b""
    parts: list[str] = []
    for record in records:
        parts.append(
            json.dumps(
                normalize_json_value(record),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        parts.append("\n")
    return "".join(parts).encode("utf-8")


def append_jsonl_payload(path: Path, payload: bytes) -> int:
    if not payload:
        return 0
    ensure_dir(str(path.parent))
    with open(path, "ab") as f:
        f.write(payload)
    return len(payload)


def append_jsonl(path: Path, records: list[dict]) -> int:
    payload = render_jsonl_payload(records)
    return append_jsonl_payload(path, payload)


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
