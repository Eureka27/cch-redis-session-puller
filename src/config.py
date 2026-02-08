"""Usage: imported by puller.py to load env configuration."""

import os


def _get_env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def load_config() -> dict:
    redis_url = _get_env("REDIS_URL")
    if not redis_url:
        raise ValueError("REDIS_URL is required")

    poll_interval = int(_get_env("POLL_INTERVAL_SECONDS", "60") or "60")
    dest_dir = _get_env("DEST_DIR", "./session")
    state_path = _get_env("STATE_PATH", "./state/state.json")
    missing_skip_seconds = int(_get_env("MISSING_SKIP_SECONDS", "300") or "300")

    return {
        "redis_url": redis_url,
        "poll_interval": poll_interval,
        "dest_dir": dest_dir,
        "state_path": state_path,
        "missing_skip_seconds": missing_skip_seconds,
    }
