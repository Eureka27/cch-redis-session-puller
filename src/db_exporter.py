"""Usage: python3 src/db_exporter.py [--once]"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from config import load_db_config
from export_state import STATE_VERSION, load_state, save_state
from output_writer import append_jsonl, build_daily_jsonl_path


DEFAULT_STATE = {
    "version": STATE_VERSION,
    "tables": {
        "message_request": {
            "cursor_ts": "1970-01-01T00:00:00+00:00",
            "cursor_id": 0,
        },
        "usage_ledger": {
            "cursor_ts": "1970-01-01T00:00:00+00:00",
            "cursor_id": 0,
        },
    },
}

MESSAGE_REQUEST_SQL = """
SELECT
  id,
  created_at,
  updated_at,
  to_jsonb(message_request) AS payload
FROM message_request
WHERE
  updated_at > %(cursor_ts)s
  OR (updated_at = %(cursor_ts)s AND id > %(cursor_id)s)
ORDER BY updated_at ASC, id ASC
LIMIT %(limit)s
"""

USAGE_LEDGER_SQL = """
SELECT
  id,
  created_at,
  to_jsonb(usage_ledger) AS payload
FROM usage_ledger
WHERE
  created_at > %(cursor_ts)s
  OR (created_at = %(cursor_ts)s AND id > %(cursor_id)s)
ORDER BY created_at ASC, id ASC
LIMIT %(limit)s
"""


def _parse_cursor_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _get_table_state(state: dict, table_name: str) -> dict:
    tables = state.setdefault("tables", {})
    entry = tables.get(table_name)
    if not isinstance(entry, dict):
        entry = DEFAULT_STATE["tables"][table_name].copy()
        tables[table_name] = entry
    return entry


def _export_table(
    conn: psycopg.Connection,
    state: dict,
    table_name: str,
    sql_text: str,
    ts_field: str,
    output_dir: str,
    batch_size: int,
) -> int:
    entry = _get_table_state(state, table_name)
    cursor_ts = _parse_cursor_ts(
        entry.get("cursor_ts") or DEFAULT_STATE["tables"][table_name]["cursor_ts"]
    )
    cursor_id = int(entry.get("cursor_id") or 0)
    exported = 0

    while True:
        with conn.cursor() as cur:
            cur.execute(
                sql_text,
                {
                    "cursor_ts": cursor_ts,
                    "cursor_id": cursor_id,
                    "limit": batch_size,
                },
            )
            rows = cur.fetchall()

        if not rows:
            break

        grouped = {}
        last_seen_ts = cursor_ts
        last_seen_id = cursor_id
        for row in rows:
            last_seen_ts = row[ts_field]
            last_seen_id = int(row["id"])
            payload = row["payload"] if isinstance(row.get("payload"), dict) else None
            if payload is None:
                continue
            partition_path = build_daily_jsonl_path(output_dir, row.get("created_at"))
            grouped.setdefault(partition_path, []).append(payload)
            exported += 1

        for path, records in grouped.items():
            append_jsonl(path, records)

        cursor_ts = last_seen_ts
        cursor_id = last_seen_id
        entry["cursor_ts"] = cursor_ts.isoformat()
        entry["cursor_id"] = cursor_id

        if len(rows) < batch_size:
            break

    return exported


def run_once(config: dict) -> dict[str, int]:
    state = load_state(config["state_path"], DEFAULT_STATE)
    results = {"message_request": 0, "usage_ledger": 0}

    with psycopg.connect(config["database_url"], autocommit=True, row_factory=dict_row) as conn:
        results["message_request"] = _export_table(
            conn=conn,
            state=state,
            table_name="message_request",
            sql_text=MESSAGE_REQUEST_SQL,
            ts_field="updated_at",
            output_dir=config["message_request_dir"],
            batch_size=config["batch_size"],
        )
        results["usage_ledger"] = _export_table(
            conn=conn,
            state=state,
            table_name="usage_ledger",
            sql_text=USAGE_LEDGER_SQL,
            ts_field="created_at",
            output_dir=config["usage_ledger_dir"],
            batch_size=config["batch_size"],
        )

    save_state(config["state_path"], state)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run once and exit")
    args = parser.parse_args()

    config = load_db_config()

    if args.once:
        run_once(config)
        return

    while True:
        run_once(config)
        time.sleep(config["poll_interval"])


if __name__ == "__main__":
    main()
