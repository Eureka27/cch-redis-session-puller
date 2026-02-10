"""Usage: imported by puller.py to build session events."""

import json
import re
from datetime import datetime, timezone


def sanitize_path_segment(value: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_.:-]", "_", value)
    if sanitized in ("", ".", ".."):
        return "unknown"
    return sanitized


def build_event(event_type: str, payload: dict, request_sequence: int | None) -> dict:
    return {
        "type": event_type,
        "at": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "requestSequence": request_sequence,
        "payload": payload,
    }


def should_ignore_user_text(text: str) -> bool:
    trimmed = text.strip()
    if not trimmed:
        return True
    return (
        trimmed.startswith("# AGENTS.md instructions")
        or trimmed.startswith("<environment_context>")
        or trimmed.startswith("# System Instructions")
        or trimmed.startswith("# Conversation")
    )


def extract_user_lines_from_conversation_text(text: str) -> list[str]:
    results: list[str] = []
    pattern = re.compile(
        r"(?:^|\r?\n)User:\s*\r?\n([\s\S]*?)(?=(?:\r?\nAssistant:|\r?\nUser:|\s*$))"
    )
    for match in pattern.finditer(text):
        candidate = (match.group(1) or "").strip()
        if candidate:
            results.append(candidate)
    return results


def _collect_text_parts(value) -> list[str]:
    parts: list[str] = []
    if isinstance(value, str):
        if value:
            parts.append(value)
        return parts
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                if item:
                    parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str) and item["text"]:
                parts.append(item["text"])
        return parts
    if isinstance(value, dict):
        if isinstance(value.get("text"), str) and value["text"]:
            parts.append(value["text"])
    return parts


def _normalize_value_to_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return None


def _normalize_content_to_text(value) -> str | None:
    parts = _collect_text_parts(value)
    if parts:
        return "".join(parts)
    return _normalize_value_to_text(value)


def extract_session_events_from_messages(messages) -> list[dict]:
    if not isinstance(messages, list):
        return []

    events: list[dict] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role") if isinstance(message.get("role"), str) else None
        content = message.get("content", message.get("parts"))

        if role == "user":
            text = _normalize_content_to_text(content)
            if text and text.strip():
                extracted = extract_user_lines_from_conversation_text(text)
                if extracted:
                    for line in extracted:
                        events.append({"type": "user_input", "payload": {"text": line}})
                elif not should_ignore_user_text(text):
                    events.append({"type": "user_input", "payload": {"text": text}})

        if message.get("type") == "input_text" and isinstance(message.get("text"), str):
            text = message["text"]
            if text.strip():
                extracted = extract_user_lines_from_conversation_text(text)
                if extracted:
                    for line in extracted:
                        events.append({"type": "user_input", "payload": {"text": line}})
                elif not should_ignore_user_text(text):
                    events.append({"type": "user_input", "payload": {"text": text}})

        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_result":
                    text = _normalize_content_to_text(part.get("content"))
                    if text and text.strip():
                        events.append({
                            "type": "tool_io",
                            "payload": {"phase": "output", "text": text},
                        })

        if message.get("role") == "tool":
            text = _normalize_content_to_text(message.get("content"))
            if text and text.strip():
                events.append({
                    "type": "tool_io",
                    "payload": {"phase": "output", "text": text},
                })

        if message.get("type") == "function_call_output":
            text = _normalize_content_to_text(
                message.get("output", message.get("content", message.get("result")))
            )
            if text and text.strip():
                events.append({
                    "type": "tool_io",
                    "payload": {"phase": "output", "text": text},
                })

    return events


def is_sse_text(text: str) -> bool:
    start = 0
    length = len(text)
    i = 0
    while i <= length:
        if i != length and text[i] != "\n":
            i += 1
            continue
        line = text[start:i].strip()
        start = i + 1
        if not line:
            i += 1
            continue
        if line.startswith(":"):
            i += 1
            continue
        return line.startswith("event:") or line.startswith("data:")
    return False


def parse_sse_data(sse_text: str) -> list[dict]:
    events: list[dict] = []
    event_name = ""
    data_lines: list[str] = []

    def flush_event():
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = ""
            data_lines = []
            return
        data_str = "\n".join(data_lines)
        try:
            data = json.loads(data_str)
        except Exception:
            data = data_str
        events.append({"event": event_name or "message", "data": data})
        event_name = ""
        data_lines = []

    for raw_line in sse_text.split("\n"):
        line = raw_line.rstrip("\n\r")
        if not line:
            flush_event()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
    flush_event()
    return events


def _append_text(parts: list[str], value) -> None:
    if isinstance(value, str) and value:
        parts.append(value)


def _parse_tool_input(value):
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed:
        return value
    try:
        return json.loads(trimmed)
    except Exception:
        return value


def tool_input_to_text(value) -> str | None:
    if isinstance(value, dict) and "command" in value and isinstance(value.get("command"), str):
        if value["command"].strip():
            return value["command"]
    return _normalize_value_to_text(value)


def _extract_from_claude_object(payload: dict, text_parts: list[str], tool_uses: list[dict]):
    content = payload.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                _append_text(text_parts, block.get("text"))
            if block.get("type") == "tool_use":
                tool_uses.append(
                    {
                        "id": block.get("id") if isinstance(block.get("id"), str) else None,
                        "name": block.get("name") if isinstance(block.get("name"), str) else None,
                        "input": block.get("input"),
                    }
                )
    else:
        _append_text(text_parts, content)


def _extract_from_openai_chat_object(
    payload: dict, text_parts: list[str], tool_uses: list[dict]
):
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            _append_text(text_parts, message.get("content"))
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    func = tool_call.get("function")
                    tool_uses.append(
                        {
                            "id": tool_call.get("id") if isinstance(tool_call.get("id"), str) else None,
                            "name": func.get("name") if isinstance(func, dict) and isinstance(func.get("name"), str) else None,
                            "input": _parse_tool_input(func.get("arguments")) if isinstance(func, dict) else None,
                        }
                    )


def _extract_from_response_api_object(
    payload: dict, text_parts: list[str], tool_uses: list[dict]
):
    output = payload.get("output")
    if not isinstance(output, list):
        return
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message" and isinstance(item.get("content"), list):
            for part in item.get("content"):
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("output_text", "text"):
                    _append_text(text_parts, part.get("text"))
        if item.get("type") == "output_text":
            _append_text(text_parts, item.get("text"))
        if item.get("type") == "function_call":
            tool_uses.append(
                {
                    "id": item.get("id") if isinstance(item.get("id"), str) else None,
                    "name": item.get("name") if isinstance(item.get("name"), str) else None,
                    "input": _parse_tool_input(item.get("arguments", item.get("input"))),
                }
            )


def _extract_from_gemini_object(payload: dict, text_parts: list[str], tool_uses: list[dict]):
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            _append_text(text_parts, part.get("text"))
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                tool_uses.append(
                    {
                        "name": function_call.get("name")
                        if isinstance(function_call.get("name"), str)
                        else None,
                        "input": function_call.get("args", function_call.get("arguments")),
                    }
                )


def _extract_from_claude_stream_events(
    events: list[dict], text_parts: list[str], tool_uses: list[dict]
):
    tool_use_by_index: dict[int, dict] = {}
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        event_type = data.get("type")
        if event_type == "content_block_start":
            content_block = data.get("content_block")
            if isinstance(content_block, dict) and content_block.get("type") == "tool_use":
                index = data.get("index") if isinstance(data.get("index"), int) else None
                tool_use = {
                    "id": content_block.get("id") if isinstance(content_block.get("id"), str) else None,
                    "name": content_block.get("name") if isinstance(content_block.get("name"), str) else None,
                    "input": content_block.get("input"),
                }
                if index is not None:
                    tool_use_by_index[index] = {**tool_use, "inputJson": None}
                else:
                    tool_uses.append(tool_use)
        if event_type == "content_block_delta":
            delta = data.get("delta") if isinstance(data.get("delta"), dict) else None
            if delta and delta.get("type") == "text_delta":
                _append_text(text_parts, delta.get("text"))
            if delta and delta.get("type") == "input_json_delta":
                index = data.get("index") if isinstance(data.get("index"), int) else None
                if index is not None and isinstance(delta.get("partial_json"), str):
                    existing = tool_use_by_index.get(index) or {"inputJson": ""}
                    existing["inputJson"] = (existing.get("inputJson") or "") + delta["partial_json"]
                    tool_use_by_index[index] = existing
    for entry in tool_use_by_index.values():
        if entry.get("input") is None and entry.get("inputJson"):
            entry["input"] = _parse_tool_input(entry.get("inputJson"))
        tool_uses.append(
            {
                "id": entry.get("id"),
                "name": entry.get("name"),
                "input": entry.get("input"),
            }
        )


def _extract_from_openai_stream_events(
    events: list[dict], text_parts: list[str], tool_uses: list[dict]
):
    tool_call_map: dict[str, dict] = {}
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        choices = data.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else None
            if delta:
                _append_text(text_parts, delta.get("content"))
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            continue
                        func = tool_call.get("function")
                        index = tool_call.get("index") if isinstance(tool_call.get("index"), int) else None
                        key = (
                            tool_call.get("id")
                            if isinstance(tool_call.get("id"), str)
                            else f"index:{index if index is not None else len(tool_call_map)}"
                        )
                        existing = tool_call_map.get(key) or {
                            "id": tool_call.get("id") if isinstance(tool_call.get("id"), str) else None,
                            "name": None,
                            "args": "",
                        }
                        if isinstance(func, dict) and isinstance(func.get("name"), str):
                            existing["name"] = func.get("name")
                        if isinstance(func, dict) and isinstance(func.get("arguments"), str):
                            existing["args"] += func.get("arguments")
                        tool_call_map[key] = existing
    for entry in tool_call_map.values():
        tool_uses.append(
            {
                "id": entry.get("id"),
                "name": entry.get("name"),
                "input": _parse_tool_input(entry.get("args")) if entry.get("args") else None,
            }
        )


def _extract_from_response_stream_events(
    events: list[dict], text_parts: list[str], tool_uses: list[dict]
):
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        event_type = data.get("type") if isinstance(data.get("type"), str) else ""
        if event_type == "response.output_text.delta":
            delta = data.get("delta") if isinstance(data.get("delta"), dict) else None
            if delta:
                _append_text(text_parts, delta.get("text"))
        if event_type == "response.output_item.added":
            item = data.get("item")
            if isinstance(item, dict):
                _extract_from_response_api_object({"output": [item]}, text_parts, tool_uses)
        if "function_call" in event_type:
            name = None
            if isinstance(data.get("name"), str):
                name = data.get("name")
            elif isinstance(data.get("function"), dict) and isinstance(data["function"].get("name"), str):
                name = data["function"]["name"]
            args = data.get("arguments")
            if args is None and isinstance(data.get("function"), dict):
                args = data["function"].get("arguments")
            if name or args is not None:
                tool_uses.append({"name": name, "input": _parse_tool_input(args)})
        if isinstance(data.get("response"), dict):
            _extract_from_response_api_object(data.get("response"), text_parts, tool_uses)


def _detect_sse_format(events: list[dict]) -> str | None:
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue

        if isinstance(data.get("choices"), list):
            return "openai"
        if isinstance(data.get("candidates"), list):
            return "gemini"
        if isinstance(data.get("output"), list):
            return "response"
        if isinstance(data.get("response"), dict):
            response_obj = data.get("response")
            if isinstance(response_obj.get("output"), list):
                return "response"
            if isinstance(response_obj.get("candidates"), list):
                return "gemini"

        event_type = data.get("type")
        if isinstance(event_type, str):
            if event_type.startswith("response."):
                return "response"
            if event_type in {
                "message_start",
                "message_stop",
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
                "message_delta",
            }:
                return "claude"
    return None


def _detect_json_format(payload: dict) -> str | None:
    if isinstance(payload.get("choices"), list):
        return "openai"
    if isinstance(payload.get("output"), list):
        return "response"
    if isinstance(payload.get("candidates"), list):
        return "gemini"
    if isinstance(payload.get("content"), list):
        return "claude"

    response_obj = payload.get("response")
    if isinstance(response_obj, dict):
        if isinstance(response_obj.get("output"), list):
            return "response"
        if isinstance(response_obj.get("candidates"), list):
            return "gemini"
    return None


def _tool_use_signature(tool_use: dict) -> str | None:
    if not isinstance(tool_use, dict):
        return None

    normalized = {
        "id": tool_use.get("id") if isinstance(tool_use.get("id"), str) else None,
        "name": tool_use.get("name") if isinstance(tool_use.get("name"), str) else None,
        "input": None,
    }
    input_value = tool_use.get("input")
    try:
        normalized["input"] = json.dumps(input_value, ensure_ascii=False, sort_keys=True)
    except Exception:
        normalized["input"] = str(input_value)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def _dedupe_tool_uses(tool_uses: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for tool_use in tool_uses:
        signature = _tool_use_signature(tool_use)
        if not signature or signature in seen:
            continue
        seen.add(signature)
        deduped.append(tool_use)
    return deduped


def extract_llm_artifacts_from_response_text(response_text: str) -> tuple[str | None, list[dict]]:
    text_parts: list[str] = []
    tool_uses: list[dict] = []

    if is_sse_text(response_text):
        events = [
            evt
            for evt in parse_sse_data(response_text)
            if not (
                isinstance(evt.get("data"), str)
                and evt.get("data").strip() == "[DONE]"
            )
        ]
        detected_format = _detect_sse_format(events)
        if detected_format == "claude":
            _extract_from_claude_stream_events(events, text_parts, tool_uses)
        elif detected_format == "openai":
            _extract_from_openai_stream_events(events, text_parts, tool_uses)
        elif detected_format == "response":
            _extract_from_response_stream_events(events, text_parts, tool_uses)
        elif detected_format == "gemini":
            for evt in events:
                if isinstance(evt.get("data"), dict):
                    _extract_from_gemini_object(
                        evt.get("data"), text_parts, tool_uses
                    )
        else:
            _extract_from_claude_stream_events(events, text_parts, tool_uses)
            _extract_from_openai_stream_events(events, text_parts, tool_uses)
            _extract_from_response_stream_events(events, text_parts, tool_uses)
            for evt in events:
                if isinstance(evt.get("data"), dict):
                    _extract_from_gemini_object(
                        evt.get("data"), text_parts, tool_uses
                    )
    else:
        try:
            parsed = json.loads(response_text)
        except Exception:
            return None, []

        if isinstance(parsed, dict):
            detected_format = _detect_json_format(parsed)
            response_obj = parsed.get("response")
            if detected_format == "claude":
                _extract_from_claude_object(parsed, text_parts, tool_uses)
            elif detected_format == "openai":
                _extract_from_openai_chat_object(parsed, text_parts, tool_uses)
            elif detected_format == "response":
                if isinstance(response_obj, dict):
                    _extract_from_response_api_object(response_obj, text_parts, tool_uses)
                _extract_from_response_api_object(parsed, text_parts, tool_uses)
            elif detected_format == "gemini":
                if isinstance(response_obj, dict):
                    _extract_from_gemini_object(response_obj, text_parts, tool_uses)
                _extract_from_gemini_object(parsed, text_parts, tool_uses)
            else:
                if isinstance(response_obj, dict):
                    _extract_from_response_api_object(response_obj, text_parts, tool_uses)
                    _extract_from_gemini_object(response_obj, text_parts, tool_uses)
                _extract_from_claude_object(parsed, text_parts, tool_uses)
                _extract_from_openai_chat_object(parsed, text_parts, tool_uses)
                _extract_from_response_api_object(parsed, text_parts, tool_uses)
                _extract_from_gemini_object(parsed, text_parts, tool_uses)

    tool_uses = _dedupe_tool_uses(tool_uses)
    answer_text = "".join(text_parts)
    if answer_text.strip():
        return answer_text, tool_uses
    return None, tool_uses
