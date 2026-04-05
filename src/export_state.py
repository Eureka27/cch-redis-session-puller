"""Shared state helpers for exporters."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path


STATE_VERSION = 1


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def load_state(path: str, default_state: dict) -> dict:
    initial = copy.deepcopy(default_state)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and data.get("version") == initial.get("version"):
                return data
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return initial


def save_state(path: str, state: dict) -> None:
    ensure_dir(str(Path(path).parent))
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, path)
