"""Usage: python3 src/puller.py [--once]"""

from __future__ import annotations

import argparse
import json
import logging
import time

import redis

from config import load_redis_config
from export_state import STATE_VERSION, load_state, save_state
from output_writer import (
    append_jsonl,
    build_session_file_path,
    normalize_json_value,
)
from session_events import (
    build_event,
    extract_response_artifacts_from_response_text,
    extract_raw_tool_events_from_messages,
    extract_session_events_from_messages,
    tool_input_to_text,
)


META_RETRY_SECONDS = 30
SNAPSHOT_KEYS = ("last_info_signature", "last_usage_signature")
STATE_DEFAULT = {"version": STATE_VERSION, "sessions": {}}
logger = logging.getLogger(__name__)


def append_session_events(dest_dir: str, session_id: str, events: list[dict]) -> str:
    if not events:
        return "empty"
    append_jsonl(build_session_file_path(dest_dir, session_id), events)
    return "appended"


def append_session_sidecars(sidecar_dir: str, session_id: str, events: list[dict]) -> str:
    if not events:
        return "empty"
    append_jsonl(build_session_file_path(sidecar_dir, session_id), events)
    return "appended"


def _get_state_entry(state: dict, session_id: str) -> dict:
    sessions = state.setdefault("sessions", {})
    entry = sessions.get(session_id)
    if not isinstance(entry, dict):
        entry = {}
        sessions[session_id] = entry
    return entry


def _should_skip_missing(
    entry: dict, channel: str, seq: int, now_ts: float, skip_seconds: int
) -> bool:
    missing = entry.get("missing")
    if not isinstance(missing, dict):
        missing = {}
        entry["missing"] = missing
    key = f"{channel}:{seq}"
    first_seen = missing.get(key)
    if first_seen is None:
        missing[key] = now_ts
        return False
    return now_ts - first_seen >= skip_seconds


def _clear_missing(entry: dict, channel: str, seq: int) -> None:
    missing = entry.get("missing")
    if isinstance(missing, dict):
        missing.pop(f"{channel}:{seq}", None)


def _parse_missing_key(key) -> tuple[str, int] | None:
    if not isinstance(key, str):
        return None
    channel, separator, seq_text = key.partition(":")
    if separator != ":" or channel not in {"msg", "rsp"}:
        return None
    try:
        seq = int(seq_text)
    except Exception:
        return None
    if seq < 0:
        return None
    return channel, seq


def _prune_missing(entry: dict, cursor_seq: int, now_ts: float, skip_seconds: int) -> None:
    missing = entry.get("missing")
    if not isinstance(missing, dict):
        return

    expire_before = now_ts - max(skip_seconds * 4, 600)
    stale_keys: list[str] = []
    for key, first_seen in missing.items():
        parsed = _parse_missing_key(key)
        if parsed is None:
            stale_keys.append(key)
            continue
        _, seq = parsed
        if seq <= cursor_seq:
            stale_keys.append(key)
            continue
        if isinstance(first_seen, (int, float)):
            if first_seen < expire_before:
                stale_keys.append(key)
        else:
            stale_keys.append(key)

    for key in stale_keys:
        missing.pop(key, None)
    if not missing:
        entry.pop("missing", None)


def _to_non_negative_int(value) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 0
    return parsed if parsed >= 0 else 0


def _get_cursor_seq(entry: dict) -> int:
    cursor_seq = entry.get("cursor_seq")
    if isinstance(cursor_seq, int) and cursor_seq >= 0:
        return cursor_seq
    last_msg_seq = _to_non_negative_int(entry.get("last_msg_seq", 0))
    last_rsp_seq = _to_non_negative_int(entry.get("last_rsp_seq", 0))
    return max(last_msg_seq, last_rsp_seq)


def _decode_redis_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return None
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return None


def _decode_redis_hash(raw_hash) -> dict[str, str]:
    if not isinstance(raw_hash, dict):
        return {}

    decoded: dict[str, str] = {}
    for raw_key, raw_value in raw_hash.items():
        key = _decode_redis_text(raw_key)
        value = _decode_redis_text(raw_value)
        if key is None or value is None:
            continue
        decoded[key] = value
    return decoded


def _read_hash(r: redis.Redis, key: str) -> dict[str, str]:
    try:
        return _decode_redis_hash(r.hgetall(key))
    except Exception:
        return {}


def _stable_signature(payload: dict[str, str]) -> str:
    return json.dumps(normalize_json_value(payload), ensure_ascii=False, sort_keys=True)


def _build_session_meta_payload(info: dict[str, str]) -> dict:
    user_name = info.get("userName", "").strip()
    if not user_name:
        return {}

    payload: dict[str, str] = {"userName": user_name}
    for field in ("keyId", "keyName", "model", "apiType"):
        value = info.get(field, "").strip()
        if value:
            payload[field] = value
    return payload


def _append_session_snapshot(
    sidecar_dir: str,
    session_id: str,
    entry: dict,
    state_key: str,
    event_type: str,
    payload: dict[str, str],
) -> None:
    if not payload:
        return
    signature = _stable_signature(payload)
    if entry.get(state_key) == signature:
        return
    append_session_sidecars(
        sidecar_dir,
        session_id,
        [build_event(event_type, payload, None)],
    )
    entry[state_key] = signature


def _extract_message_events(raw_messages, seq: int) -> list[dict]:
    try:
        messages = json.loads(raw_messages)
    except Exception:
        return []
    if messages is None:
        return []

    events: list[dict] = []
    for ev in extract_session_events_from_messages(messages):
        events.append(build_event(ev["type"], ev["payload"], seq))
    for ev in extract_raw_tool_events_from_messages(messages):
        events.append(build_event(ev["type"], ev["payload"], seq))
    return events


def _extract_response_events(raw_response, seq: int) -> list[dict]:
    response_text = _decode_redis_text(raw_response)
    if not response_text:
        return []

    answer_text, tool_uses, raw_events = extract_response_artifacts_from_response_text(
        response_text
    )
    events: list[dict] = []
    for raw_event in raw_events:
        events.append(build_event(raw_event["type"], raw_event["payload"], seq))
    seen_inputs: set[str] = set()
    for tool_use in tool_uses:
        input_text = tool_use.get("input")
        if isinstance(input_text, (dict, list)) or isinstance(input_text, str):
            text = tool_input_to_text(input_text)
        else:
            text = None
        name = tool_use.get("name") if isinstance(tool_use.get("name"), str) else None
        if name and text:
            combined = f"{name}: {text}"
        else:
            combined = name or text
        if not combined or not combined.strip() or combined in seen_inputs:
            continue
        seen_inputs.add(combined)
        events.append(build_event("tool_io", {"phase": "input", "text": combined}, seq))

    if answer_text:
        events.append(build_event("llm_answer", {"text": answer_text}, seq))

    return events


def _read_seq_records(r: redis.Redis, session_id: str, seq: int) -> dict[str, bytes | str | None]:
    keys = {
        "messages": f"session:{session_id}:req:{seq}:messages",
        "response": f"session:{session_id}:req:{seq}:response",
        "request_body": f"session:{session_id}:req:{seq}:requestBody",
        "special_settings": f"session:{session_id}:req:{seq}:specialSettings",
        "client_request_meta": f"session:{session_id}:req:{seq}:clientReqMeta",
        "upstream_request_meta": f"session:{session_id}:req:{seq}:upstreamReqMeta",
        "upstream_response_meta": f"session:{session_id}:req:{seq}:upstreamResMeta",
        "request_headers": f"session:{session_id}:req:{seq}:reqHeaders",
        "response_headers": f"session:{session_id}:req:{seq}:resHeaders",
    }
    try:
        values = r.mget(list(keys.values()))
        if isinstance(values, list) and len(values) == len(keys):
            return {
                name: values[index]
                for index, name in enumerate(keys.keys())
            }
    except Exception:
        pass
    return {name: r.get(key) for name, key in keys.items()}


def _parse_json_value(raw_value):
    text = _decode_redis_text(raw_value)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _extract_sidecar_events(records: dict[str, bytes | str | None], seq: int) -> list[dict]:
    events: list[dict] = []

    request_body = _parse_json_value(records.get("request_body"))
    if request_body is not None:
        events.append(build_event("request_body", {"body": request_body}, seq))

    special_settings = _parse_json_value(records.get("special_settings"))
    if special_settings is not None:
        events.append(
            build_event("request_special_settings", {"items": special_settings}, seq)
        )

    client_request_meta = _parse_json_value(records.get("client_request_meta"))
    if isinstance(client_request_meta, dict):
        events.append(build_event("client_request_meta", client_request_meta, seq))

    upstream_request_meta = _parse_json_value(records.get("upstream_request_meta"))
    if isinstance(upstream_request_meta, dict):
        events.append(build_event("upstream_request_meta", upstream_request_meta, seq))

    upstream_response_meta = _parse_json_value(records.get("upstream_response_meta"))
    if isinstance(upstream_response_meta, dict):
        events.append(build_event("upstream_response_meta", upstream_response_meta, seq))

    request_headers = _parse_json_value(records.get("request_headers"))
    if isinstance(request_headers, dict):
        events.append(build_event("request_headers", {"headers": request_headers}, seq))

    response_headers = _parse_json_value(records.get("response_headers"))
    if isinstance(response_headers, dict):
        events.append(build_event("response_headers", {"headers": response_headers}, seq))

    response_body = _decode_redis_text(records.get("response"))
    if response_body:
        events.append(build_event("response_body", {"text": response_body}, seq))

    return events


def process_session(
    r: redis.Redis,
    state: dict,
    session_id: str,
    dest_dir: str,
    sidecar_dir: str,
    now_ts: float,
    skip_seconds: int,
) -> None:
    entry = _get_state_entry(state, session_id)
    cursor_seq = _get_cursor_seq(entry)
    _prune_missing(entry, cursor_seq, now_ts, skip_seconds)

    session_info = _read_hash(r, f"session:{session_id}:info")
    session_usage = _read_hash(r, f"session:{session_id}:usage")
    _append_session_snapshot(
        sidecar_dir, session_id, entry, "last_info_signature", "session_info", session_info
    )
    _append_session_snapshot(
        sidecar_dir, session_id, entry, "last_usage_signature", "session_usage", session_usage
    )

    seq_value = r.get(f"session:{session_id}:seq")
    if not seq_value:
        return
    try:
        max_seq = int(seq_value)
    except Exception:
        return

    if not entry.get("meta_written"):
        retry_at = entry.get("meta_retry_at")
        should_try_meta = True
        if isinstance(retry_at, (int, float)) and now_ts < retry_at:
            should_try_meta = False

        if should_try_meta:
            session_meta_payload = _build_session_meta_payload(session_info)
            if session_meta_payload:
                append_session_events(
                    dest_dir,
                    session_id,
                    [build_event("session_meta", session_meta_payload, None)],
                )
                entry["meta_written"] = True
                entry.pop("meta_retry_at", None)
            else:
                entry["meta_retry_at"] = now_ts + META_RETRY_SECONDS

    if cursor_seq >= max_seq:
        entry["cursor_seq"] = cursor_seq
        entry["last_msg_seq"] = cursor_seq
        entry["last_rsp_seq"] = cursor_seq
        return

    for seq in range(cursor_seq + 1, max_seq + 1):
        records = _read_seq_records(r, session_id, seq)
        raw_messages = records.get("messages")
        raw_response = records.get("response")

        messages_ready = raw_messages is not None
        response_ready = raw_response is not None

        messages_skipped = False
        response_skipped = False
        if messages_ready:
            _clear_missing(entry, "msg", seq)
        else:
            messages_skipped = _should_skip_missing(entry, "msg", seq, now_ts, skip_seconds)

        if response_ready:
            _clear_missing(entry, "rsp", seq)
        else:
            response_skipped = _should_skip_missing(entry, "rsp", seq, now_ts, skip_seconds)

        if (not messages_ready and not messages_skipped) or (
            not response_ready and not response_skipped
        ):
            break

        events: list[dict] = []
        sidecars: list[dict] = []
        if messages_ready:
            events.extend(_extract_message_events(raw_messages, seq))
        if response_ready:
            events.extend(_extract_response_events(raw_response, seq))
        sidecars.extend(_extract_sidecar_events(records, seq))
        append_session_events(dest_dir, session_id, events)
        append_session_sidecars(sidecar_dir, session_id, sidecars)

        if not messages_ready:
            _clear_missing(entry, "msg", seq)
        if not response_ready:
            _clear_missing(entry, "rsp", seq)

        cursor_seq = seq

    _prune_missing(entry, cursor_seq, now_ts, skip_seconds)
    entry["cursor_seq"] = cursor_seq
    entry["last_msg_seq"] = cursor_seq
    entry["last_rsp_seq"] = cursor_seq


def scan_sessions(r: redis.Redis) -> list[str]:
    session_ids: list[str] = []
    cursor = 0
    pattern = "session:*:info"
    while True:
        cursor, keys = r.scan(cursor=cursor, match=pattern, count=1000)
        if keys:
            for key in keys:
                decoded = _decode_redis_text(key)
                if not decoded:
                    continue
                if decoded.startswith("session:") and decoded.endswith(":info"):
                    session_id = decoded[len("session:") : -len(":info")]
                    if session_id:
                        session_ids.append(session_id)
        if cursor == 0:
            break
    return session_ids


def run_once(config: dict) -> None:
    r = redis.Redis.from_url(config["redis_url"], decode_responses=False)
    state = load_state(config["state_path"], STATE_DEFAULT)

    now_ts = time.time()
    session_ids = scan_sessions(r)
    for session_id in session_ids:
        process_session(
            r,
            state,
            session_id,
            config["dest_dir"],
            config["sidecar_dir"],
            now_ts,
            config["missing_skip_seconds"],
        )

    save_state(config["state_path"], state)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run once and exit")
    args = parser.parse_args()

    config = load_redis_config()

    if args.once:
        run_once(config)
        return

    while True:
        run_once(config)
        time.sleep(config["poll_interval"])


if __name__ == "__main__":
    main()
