"""Usage: python3 src/puller.py [--once]"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

import redis

from config import load_config
from session_events import (
    build_event,
    extract_llm_artifacts_from_response_text,
    extract_session_events_from_messages,
    sanitize_path_segment,
    tool_input_to_text,
)


STATE_VERSION = 1
logger = logging.getLogger(__name__)


def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                if data.get("version") != STATE_VERSION:
                    return {"version": STATE_VERSION, "sessions": {}}
                return data
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {"version": STATE_VERSION, "sessions": {}}


def save_state(path: str, state: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, path)


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def _build_session_file_path(dest_dir: str, session_id: str) -> Path:
    safe_id = sanitize_path_segment(session_id)
    return Path(dest_dir) / f"{safe_id}.json"


def append_session_events(
    dest_dir: str, session_id: str, events: list[dict]
) -> str:
    if not events:
        return "empty"

    file_path = _build_session_file_path(dest_dir, session_id)
    ensure_dir(str(file_path.parent))
    with open(file_path, "a", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
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
    return events


def _extract_response_events(raw_response, seq: int) -> list[dict]:
    try:
        response_text = (
            raw_response.decode("utf-8")
            if isinstance(raw_response, bytes)
            else str(raw_response)
        )
    except Exception:
        return []

    if not response_text:
        return []

    answer_text, tool_uses = extract_llm_artifacts_from_response_text(response_text)
    events: list[dict] = []
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


def _read_seq_payloads(
    r: redis.Redis, session_id: str, seq: int
) -> tuple[bytes | str | None, bytes | str | None]:
    msg_key = f"session:{session_id}:req:{seq}:messages"
    rsp_key = f"session:{session_id}:req:{seq}:response"
    try:
        values = r.mget([msg_key, rsp_key])
        if isinstance(values, list) and len(values) == 2:
            return values[0], values[1]
    except Exception:
        pass
    return r.get(msg_key), r.get(rsp_key)


def process_session(
    r: redis.Redis,
    state: dict,
    session_id: str,
    dest_dir: str,
    now_ts: float,
    skip_seconds: int,
) -> None:
    entry = _get_state_entry(state, session_id)
    cursor_seq = _get_cursor_seq(entry)
    _prune_missing(entry, cursor_seq, now_ts, skip_seconds)

    seq_value = r.get(f"session:{session_id}:seq")
    if not seq_value:
        return
    try:
        max_seq = int(seq_value)
    except Exception:
        return

    if cursor_seq >= max_seq:
        entry["cursor_seq"] = cursor_seq
        entry["last_msg_seq"] = cursor_seq
        entry["last_rsp_seq"] = cursor_seq
        return

    for seq in range(cursor_seq + 1, max_seq + 1):
        raw_messages, raw_response = _read_seq_payloads(r, session_id, seq)

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
        if messages_ready:
            events.extend(_extract_message_events(raw_messages, seq))
        if response_ready:
            events.extend(_extract_response_events(raw_response, seq))
        append_session_events(dest_dir, session_id, events)

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
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                if not isinstance(key, str):
                    continue
                if key.startswith("session:") and key.endswith(":info"):
                    session_id = key[len("session:") : -len(":info")]
                    if session_id:
                        session_ids.append(session_id)
        if cursor == 0:
            break
    return session_ids


def run_once(config: dict) -> None:
    r = redis.Redis.from_url(config["redis_url"], decode_responses=False)
    ensure_dir(config["dest_dir"])
    ensure_dir(str(Path(config["state_path"]).parent))
    state = load_state(config["state_path"])

    now_ts = time.time()
    session_ids = scan_sessions(r)
    for session_id in session_ids:
        process_session(
            r,
            state,
            session_id,
            config["dest_dir"],
            now_ts,
            config["missing_skip_seconds"],
        )

    save_state(config["state_path"], state)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run once and exit")
    args = parser.parse_args()

    config = load_config()

    if args.once:
        run_once(config)
        return

    while True:
        run_once(config)
        time.sleep(config["poll_interval"])


if __name__ == "__main__":
    main()
