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


def _string_or_none(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _json_signature(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _append_raw_tool_event(
    events: list[dict],
    event_type: str,
    raw_block,
    *,
    source: str,
    provider_protocol: str | None = None,
    message_index: int | None = None,
    message_role: str | None = None,
    message_type: str | None = None,
    content_index: int | None = None,
    tool_call_id: str | None = None,
    tool_use_id: str | None = None,
    tool_name: str | None = None,
) -> None:
    payload = {
        "source": source,
        "rawBlock": raw_block,
    }
    if provider_protocol:
        payload["providerProtocol"] = provider_protocol
    if message_index is not None:
        payload["messageIndex"] = message_index
    if message_role:
        payload["messageRole"] = message_role
    if message_type:
        payload["messageType"] = message_type
    if content_index is not None:
        payload["contentIndex"] = content_index
    if tool_call_id:
        payload["toolCallId"] = tool_call_id
    if tool_use_id:
        payload["toolUseId"] = tool_use_id
    if tool_name:
        payload["toolName"] = tool_name
    events.append({"type": event_type, "payload": payload})


def _dedupe_raw_tool_events(events: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for event in events:
        signature = _json_signature(
            {
                "type": event.get("type"),
                "payload": event.get("payload"),
            }
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(event)
    return deduped


def _extract_raw_tool_events_from_content_part(
    events: list[dict],
    part: dict,
    *,
    source: str,
    provider_protocol: str,
    message_index: int | None,
    message_role: str | None,
    message_type: str | None,
    content_index: int | None,
) -> None:
    part_type = _string_or_none(part.get("type"))
    if part_type == "tool_use":
        _append_raw_tool_event(
            events,
            "tool_call_raw",
            part,
            source=source,
            provider_protocol=provider_protocol,
            message_index=message_index,
            message_role=message_role,
            message_type=message_type,
            content_index=content_index,
            tool_call_id=_string_or_none(part.get("id")),
            tool_name=_string_or_none(part.get("name")),
        )
    if part_type == "tool_result":
        _append_raw_tool_event(
            events,
            "tool_result_raw",
            part,
            source=source,
            provider_protocol=provider_protocol,
            message_index=message_index,
            message_role=message_role,
            message_type=message_type,
            content_index=content_index,
            tool_use_id=_string_or_none(part.get("tool_use_id")),
            tool_name=_string_or_none(part.get("name")),
        )

    function_call = part.get("functionCall")
    if isinstance(function_call, dict):
        _append_raw_tool_event(
            events,
            "tool_call_raw",
            part,
            source=source,
            provider_protocol="gemini",
            message_index=message_index,
            message_role=message_role,
            message_type=message_type,
            content_index=content_index,
            tool_name=_string_or_none(function_call.get("name")),
        )

    function_response = part.get("functionResponse")
    if isinstance(function_response, dict):
        _append_raw_tool_event(
            events,
            "tool_result_raw",
            part,
            source=source,
            provider_protocol="gemini",
            message_index=message_index,
            message_role=message_role,
            message_type=message_type,
            content_index=content_index,
            tool_name=_string_or_none(function_response.get("name")),
        )


def extract_raw_tool_events_from_messages(messages) -> list[dict]:
    if not isinstance(messages, list):
        return []

    events: list[dict] = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue

        message_role = _string_or_none(message.get("role"))
        message_type = _string_or_none(message.get("type"))
        content = message.get("content", message.get("parts"))

        if isinstance(content, list):
            for content_index, part in enumerate(content):
                if not isinstance(part, dict):
                    continue
                _extract_raw_tool_events_from_content_part(
                    events,
                    part,
                    source="messages",
                    provider_protocol="claude",
                    message_index=message_index,
                    message_role=message_role,
                    message_type=message_type,
                    content_index=content_index,
                )

        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for content_index, tool_call in enumerate(tool_calls):
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                _append_raw_tool_event(
                    events,
                    "tool_call_raw",
                    tool_call,
                    source="messages",
                    provider_protocol="openai_chat",
                    message_index=message_index,
                    message_role=message_role,
                    message_type=message_type,
                    content_index=content_index,
                    tool_call_id=_string_or_none(tool_call.get("id")),
                    tool_name=_string_or_none(function.get("name"))
                    if isinstance(function, dict)
                    else None,
                )

        function_call = message.get("function_call")
        if isinstance(function_call, dict):
            _append_raw_tool_event(
                events,
                "tool_call_raw",
                function_call,
                source="messages",
                provider_protocol="openai_chat",
                message_index=message_index,
                message_role=message_role,
                message_type=message_type,
                tool_call_id=_string_or_none(message.get("tool_call_id")),
                tool_name=_string_or_none(function_call.get("name")),
            )

        if message_role == "tool":
            _append_raw_tool_event(
                events,
                "tool_result_raw",
                message,
                source="messages",
                provider_protocol="openai_chat",
                message_index=message_index,
                message_role=message_role,
                message_type=message_type,
                tool_use_id=_string_or_none(message.get("tool_call_id")),
                tool_name=_string_or_none(message.get("name")),
            )

        if message_type == "function_call":
            _append_raw_tool_event(
                events,
                "tool_call_raw",
                message,
                source="messages",
                provider_protocol="response_api",
                message_index=message_index,
                message_role=message_role,
                message_type=message_type,
                tool_call_id=_string_or_none(message.get("call_id"))
                or _string_or_none(message.get("id")),
                tool_name=_string_or_none(message.get("name")),
            )

        if message_type == "function_call_output":
            _append_raw_tool_event(
                events,
                "tool_result_raw",
                message,
                source="messages",
                provider_protocol="response_api",
                message_index=message_index,
                message_role=message_role,
                message_type=message_type,
                tool_use_id=_string_or_none(message.get("call_id"))
                or _string_or_none(message.get("id")),
                tool_name=_string_or_none(message.get("name")),
            )

    return _dedupe_raw_tool_events(events)


def extract_session_events_from_messages(messages) -> list[dict]:
    if not isinstance(messages, list):
        return []

    last_user_texts: list[str] = []
    tool_output_events: list[dict] = []
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
                    last_user_texts = extracted
                elif not should_ignore_user_text(text):
                    last_user_texts = [text]

        if message.get("type") == "input_text" and isinstance(message.get("text"), str):
            text = message["text"]
            if text.strip():
                extracted = extract_user_lines_from_conversation_text(text)
                if extracted:
                    last_user_texts = extracted
                elif not should_ignore_user_text(text):
                    last_user_texts = [text]

        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_result":
                    text = _normalize_content_to_text(part.get("content"))
                    if text and text.strip():
                        tool_output_events.append({
                            "type": "tool_io",
                            "payload": {"phase": "output", "text": text},
                        })

        if message.get("role") == "tool":
            text = _normalize_content_to_text(message.get("content"))
            if text and text.strip():
                tool_output_events.append({
                    "type": "tool_io",
                    "payload": {"phase": "output", "text": text},
                })

        if message.get("type") == "function_call_output":
            text = _normalize_content_to_text(
                message.get("output", message.get("content", message.get("result")))
            )
            if text and text.strip():
                tool_output_events.append({
                    "type": "tool_io",
                    "payload": {"phase": "output", "text": text},
                })

    user_events = [
        {"type": "user_input", "payload": {"text": text}}
        for text in last_user_texts
        if isinstance(text, str) and text.strip()
    ]
    return user_events + tool_output_events


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
        if item.get("type") == "message":
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") in ("output_text", "text"):
                        _append_text(text_parts, part.get("text"))
            else:
                _append_text(text_parts, _normalize_content_to_text(content))
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


def _extract_raw_tool_events_from_claude_object(payload: dict, raw_events: list[dict]):
    content = payload.get("content")
    if not isinstance(content, list):
        return
    for content_index, block in enumerate(content):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        _append_raw_tool_event(
            raw_events,
            "tool_call_raw",
            block,
            source="response_body",
            provider_protocol="claude",
            message_index=0,
            message_role="assistant",
            content_index=content_index,
            tool_call_id=_string_or_none(block.get("id")),
            tool_name=_string_or_none(block.get("name")),
        )


def _extract_raw_tool_events_from_openai_chat_object(payload: dict, raw_events: list[dict]):
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return
    for choice_index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue

        message_role = _string_or_none(message.get("role"))
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for content_index, tool_call in enumerate(tool_calls):
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                _append_raw_tool_event(
                    raw_events,
                    "tool_call_raw",
                    tool_call,
                    source="response_body",
                    provider_protocol="openai_chat",
                    message_index=choice_index,
                    message_role=message_role,
                    content_index=content_index,
                    tool_call_id=_string_or_none(tool_call.get("id")),
                    tool_name=_string_or_none(function.get("name"))
                    if isinstance(function, dict)
                    else None,
                )

        function_call = message.get("function_call")
        if isinstance(function_call, dict):
            _append_raw_tool_event(
                raw_events,
                "tool_call_raw",
                function_call,
                source="response_body",
                provider_protocol="openai_chat",
                message_index=choice_index,
                message_role=message_role,
                tool_call_id=_string_or_none(message.get("tool_call_id")),
                tool_name=_string_or_none(function_call.get("name")),
            )


def _extract_raw_tool_events_from_response_api_object(payload: dict, raw_events: list[dict]):
    output = payload.get("output")
    if not isinstance(output, list):
        return
    for item_index, item in enumerate(output):
        if not isinstance(item, dict):
            continue
        item_type = _string_or_none(item.get("type"))
        if item_type == "function_call":
            _append_raw_tool_event(
                raw_events,
                "tool_call_raw",
                item,
                source="response_body",
                provider_protocol="response_api",
                message_index=item_index,
                message_type=item_type,
                tool_call_id=_string_or_none(item.get("call_id"))
                or _string_or_none(item.get("id")),
                tool_name=_string_or_none(item.get("name")),
            )
        if item_type == "function_call_output":
            _append_raw_tool_event(
                raw_events,
                "tool_result_raw",
                item,
                source="response_body",
                provider_protocol="response_api",
                message_index=item_index,
                message_type=item_type,
                tool_use_id=_string_or_none(item.get("call_id"))
                or _string_or_none(item.get("id")),
                tool_name=_string_or_none(item.get("name")),
            )


def _extract_raw_tool_events_from_gemini_object(payload: dict, raw_events: list[dict]):
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return
    for candidate_index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            continue
        for content_index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                _append_raw_tool_event(
                    raw_events,
                    "tool_call_raw",
                    part,
                    source="response_body",
                    provider_protocol="gemini",
                    message_index=candidate_index,
                    content_index=content_index,
                    tool_name=_string_or_none(function_call.get("name")),
                )
            function_response = part.get("functionResponse")
            if isinstance(function_response, dict):
                _append_raw_tool_event(
                    raw_events,
                    "tool_result_raw",
                    part,
                    source="response_body",
                    provider_protocol="gemini",
                    message_index=candidate_index,
                    content_index=content_index,
                    tool_name=_string_or_none(function_response.get("name")),
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
    final_response_obj: dict | None = None
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        response_obj = data.get("response")
        if isinstance(response_obj, dict):
            final_response_obj = response_obj

    if final_response_obj is not None:
        before_len = len(text_parts)
        _extract_from_response_api_object(final_response_obj, text_parts, tool_uses)
        if len(text_parts) != before_len:
            return

    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        event_type = data.get("type") if isinstance(data.get("type"), str) else ""
        if event_type in {"response.output_text.delta", "response.output_text.done"}:
            delta = data.get("delta")
            if isinstance(delta, str):
                _append_text(text_parts, delta)
            elif isinstance(delta, dict):
                _append_text(text_parts, delta.get("text"))
        if event_type in {"response.output_item.added", "response.output_item.done"}:
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


def _extract_raw_tool_events_from_claude_stream_events(
    events: list[dict], raw_events: list[dict]
):
    tool_use_by_index: dict[int, dict] = {}
    unordered_tool_uses: list[dict] = []
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        event_type = data.get("type")
        if event_type == "content_block_start":
            content_block = data.get("content_block")
            if isinstance(content_block, dict) and content_block.get("type") == "tool_use":
                index = data.get("index") if isinstance(data.get("index"), int) else None
                block = {
                    "type": "tool_use",
                    "id": content_block.get("id") if isinstance(content_block.get("id"), str) else None,
                    "name": content_block.get("name") if isinstance(content_block.get("name"), str) else None,
                    "input": content_block.get("input"),
                    "inputJson": None,
                }
                if index is None:
                    unordered_tool_uses.append(block)
                else:
                    tool_use_by_index[index] = block
        if event_type == "content_block_delta":
            delta = data.get("delta") if isinstance(data.get("delta"), dict) else None
            index = data.get("index") if isinstance(data.get("index"), int) else None
            if delta and delta.get("type") == "input_json_delta":
                if index is None or not isinstance(delta.get("partial_json"), str):
                    continue
                existing = tool_use_by_index.get(index)
                if not existing:
                    continue
                existing["inputJson"] = (existing.get("inputJson") or "") + delta["partial_json"]
                tool_use_by_index[index] = existing

    for content_index, block in sorted(tool_use_by_index.items()):
        raw_block = {"type": "tool_use"}
        if block.get("id") is not None:
            raw_block["id"] = block.get("id")
        if block.get("name") is not None:
            raw_block["name"] = block.get("name")
        input_value = block.get("input")
        if input_value is None and block.get("inputJson"):
            input_value = _parse_tool_input(block.get("inputJson"))
        if input_value is not None:
            raw_block["input"] = input_value
        _append_raw_tool_event(
            raw_events,
            "tool_call_raw",
            raw_block,
            source="response_body",
            provider_protocol="claude",
            message_index=0,
            message_role="assistant",
            content_index=content_index,
            tool_call_id=_string_or_none(block.get("id")),
            tool_name=_string_or_none(block.get("name")),
        )

    for block in unordered_tool_uses:
        raw_block = {"type": "tool_use"}
        if block.get("id") is not None:
            raw_block["id"] = block.get("id")
        if block.get("name") is not None:
            raw_block["name"] = block.get("name")
        if block.get("input") is not None:
            raw_block["input"] = block.get("input")
        _append_raw_tool_event(
            raw_events,
            "tool_call_raw",
            raw_block,
            source="response_body",
            provider_protocol="claude",
            message_index=0,
            message_role="assistant",
            tool_call_id=_string_or_none(block.get("id")),
            tool_name=_string_or_none(block.get("name")),
        )


def _extract_raw_tool_events_from_openai_stream_events(
    events: list[dict], raw_events: list[dict]
):
    tool_call_map: dict[str, dict] = {}
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        choices = data.get("choices")
        if not isinstance(choices, list):
            continue
        for choice_index, choice in enumerate(choices):
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            tool_calls = delta.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                content_index = (
                    tool_call.get("index") if isinstance(tool_call.get("index"), int) else None
                )
                key = (
                    tool_call.get("id")
                    if isinstance(tool_call.get("id"), str)
                    else f"{choice_index}:{content_index if content_index is not None else len(tool_call_map)}"
                )
                existing = tool_call_map.get(key) or {
                    "id": tool_call.get("id") if isinstance(tool_call.get("id"), str) else None,
                    "type": tool_call.get("type") if isinstance(tool_call.get("type"), str) else "function",
                    "name": None,
                    "arguments": "",
                    "messageIndex": choice_index,
                    "contentIndex": content_index,
                }
                if isinstance(function, dict) and isinstance(function.get("name"), str):
                    existing["name"] = function.get("name")
                if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                    existing["arguments"] += function.get("arguments")
                tool_call_map[key] = existing

    for entry in tool_call_map.values():
        raw_block = {
            "type": entry.get("type") or "function",
            "function": {},
        }
        if entry.get("id") is not None:
            raw_block["id"] = entry.get("id")
        if entry.get("name") is not None:
            raw_block["function"]["name"] = entry.get("name")
        if entry.get("arguments"):
            raw_block["function"]["arguments"] = entry.get("arguments")
        _append_raw_tool_event(
            raw_events,
            "tool_call_raw",
            raw_block,
            source="response_body",
            provider_protocol="openai_chat",
            message_index=entry.get("messageIndex"),
            message_role="assistant",
            content_index=entry.get("contentIndex"),
            tool_call_id=_string_or_none(entry.get("id")),
            tool_name=_string_or_none(entry.get("name")),
        )


def _extract_raw_tool_events_from_response_stream_events(
    events: list[dict], raw_events: list[dict]
):
    final_response_obj: dict | None = None
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        response_obj = data.get("response")
        if isinstance(response_obj, dict):
            final_response_obj = response_obj

    if final_response_obj is not None:
        _extract_raw_tool_events_from_response_api_object(final_response_obj, raw_events)

    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        event_type = data.get("type") if isinstance(data.get("type"), str) else ""
        if event_type in {"response.output_item.added", "response.output_item.done"}:
            item = data.get("item")
            if not isinstance(item, dict):
                continue
            item_type = _string_or_none(item.get("type"))
            if item_type == "function_call":
                _append_raw_tool_event(
                    raw_events,
                    "tool_call_raw",
                    item,
                    source="response_body",
                    provider_protocol="response_api",
                    message_index=data.get("output_index")
                    if isinstance(data.get("output_index"), int)
                    else None,
                    message_type=item_type,
                    tool_call_id=_string_or_none(item.get("call_id"))
                    or _string_or_none(item.get("id")),
                    tool_name=_string_or_none(item.get("name")),
                )
            if item_type == "function_call_output":
                _append_raw_tool_event(
                    raw_events,
                    "tool_result_raw",
                    item,
                    source="response_body",
                    provider_protocol="response_api",
                    message_index=data.get("output_index")
                    if isinstance(data.get("output_index"), int)
                    else None,
                    message_type=item_type,
                    tool_use_id=_string_or_none(item.get("call_id"))
                    or _string_or_none(item.get("id")),
                    tool_name=_string_or_none(item.get("name")),
                )
        elif "function_call" in event_type:
            raw_block = {}
            for key in ("id", "call_id", "name", "arguments", "input", "type"):
                if key in data:
                    raw_block[key] = data.get(key)
            function = data.get("function")
            if isinstance(function, dict):
                raw_block["function"] = function
            if not raw_block:
                continue
            tool_name = _string_or_none(raw_block.get("name"))
            if tool_name is None and isinstance(function, dict):
                tool_name = _string_or_none(function.get("name"))
            _append_raw_tool_event(
                raw_events,
                "tool_call_raw",
                raw_block,
                source="response_body",
                provider_protocol="response_api",
                message_type=event_type,
                tool_call_id=_string_or_none(raw_block.get("call_id"))
                or _string_or_none(raw_block.get("id")),
                tool_name=tool_name,
            )


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


def extract_response_artifacts_from_response_text(
    response_text: str,
) -> tuple[str | None, list[dict], list[dict]]:
    text_parts: list[str] = []
    tool_uses: list[dict] = []
    raw_events: list[dict] = []

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
            _extract_raw_tool_events_from_claude_stream_events(events, raw_events)
        elif detected_format == "openai":
            _extract_from_openai_stream_events(events, text_parts, tool_uses)
            _extract_raw_tool_events_from_openai_stream_events(events, raw_events)
        elif detected_format == "response":
            _extract_from_response_stream_events(events, text_parts, tool_uses)
            _extract_raw_tool_events_from_response_stream_events(events, raw_events)
        elif detected_format == "gemini":
            for evt in events:
                if isinstance(evt.get("data"), dict):
                    _extract_from_gemini_object(evt.get("data"), text_parts, tool_uses)
                    _extract_raw_tool_events_from_gemini_object(evt.get("data"), raw_events)
        else:
            _extract_from_claude_stream_events(events, text_parts, tool_uses)
            _extract_from_openai_stream_events(events, text_parts, tool_uses)
            _extract_from_response_stream_events(events, text_parts, tool_uses)
            _extract_raw_tool_events_from_claude_stream_events(events, raw_events)
            _extract_raw_tool_events_from_openai_stream_events(events, raw_events)
            _extract_raw_tool_events_from_response_stream_events(events, raw_events)
            for evt in events:
                if isinstance(evt.get("data"), dict):
                    _extract_from_gemini_object(evt.get("data"), text_parts, tool_uses)
                    _extract_raw_tool_events_from_gemini_object(evt.get("data"), raw_events)
        raw_events = _dedupe_raw_tool_events(raw_events)
        tool_uses = _dedupe_tool_uses(tool_uses)
        answer_text = "".join(text_parts)
        return (answer_text if answer_text.strip() else None, tool_uses, raw_events)

    try:
        parsed = json.loads(response_text)
    except Exception:
        return None, [], []

    if not isinstance(parsed, dict):
        return None, [], []

    detected_format = _detect_json_format(parsed)
    response_obj = parsed.get("response")
    if detected_format == "claude":
        _extract_from_claude_object(parsed, text_parts, tool_uses)
        _extract_raw_tool_events_from_claude_object(parsed, raw_events)
    elif detected_format == "openai":
        _extract_from_openai_chat_object(parsed, text_parts, tool_uses)
        _extract_raw_tool_events_from_openai_chat_object(parsed, raw_events)
    elif detected_format == "response":
        if isinstance(response_obj, dict):
            _extract_from_response_api_object(response_obj, text_parts, tool_uses)
            _extract_raw_tool_events_from_response_api_object(response_obj, raw_events)
        _extract_from_response_api_object(parsed, text_parts, tool_uses)
        _extract_raw_tool_events_from_response_api_object(parsed, raw_events)
    elif detected_format == "gemini":
        if isinstance(response_obj, dict):
            _extract_from_gemini_object(response_obj, text_parts, tool_uses)
            _extract_raw_tool_events_from_gemini_object(response_obj, raw_events)
        _extract_from_gemini_object(parsed, text_parts, tool_uses)
        _extract_raw_tool_events_from_gemini_object(parsed, raw_events)
    else:
        if isinstance(response_obj, dict):
            _extract_from_response_api_object(response_obj, text_parts, tool_uses)
            _extract_from_gemini_object(response_obj, text_parts, tool_uses)
            _extract_raw_tool_events_from_response_api_object(response_obj, raw_events)
            _extract_raw_tool_events_from_gemini_object(response_obj, raw_events)
        _extract_from_claude_object(parsed, text_parts, tool_uses)
        _extract_from_openai_chat_object(parsed, text_parts, tool_uses)
        _extract_from_response_api_object(parsed, text_parts, tool_uses)
        _extract_from_gemini_object(parsed, text_parts, tool_uses)
        _extract_raw_tool_events_from_claude_object(parsed, raw_events)
        _extract_raw_tool_events_from_openai_chat_object(parsed, raw_events)
        _extract_raw_tool_events_from_response_api_object(parsed, raw_events)
        _extract_raw_tool_events_from_gemini_object(parsed, raw_events)

    raw_events = _dedupe_raw_tool_events(raw_events)
    tool_uses = _dedupe_tool_uses(tool_uses)
    answer_text = "".join(text_parts)
    return (answer_text if answer_text.strip() else None, tool_uses, raw_events)


def extract_raw_tool_events_from_response_text(response_text: str) -> list[dict]:
    _, _, raw_events = extract_response_artifacts_from_response_text(response_text)
    return raw_events


def extract_llm_artifacts_from_response_text(response_text: str) -> tuple[str | None, list[dict]]:
    answer_text, tool_uses, _ = extract_response_artifacts_from_response_text(response_text)
    return answer_text, tool_uses
