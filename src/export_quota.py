"""Shared export-root quota enforcement."""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
from pathlib import Path

from export_state import ensure_dir
from output_writer import append_jsonl_payload


class ExportRootQuota:
    def __init__(self, export_root: str, max_bytes: int, *, logger=None) -> None:
        self.export_root = Path(export_root)
        self.max_bytes = int(max_bytes or 0)
        self.logger = logger or logging.getLogger(__name__)
        self.lock_path = self.export_root / ".export-max-bytes.lock"
        self._paused = False

    @property
    def enabled(self) -> bool:
        return self.max_bytes > 0

    def available_bytes(self) -> int | None:
        if not self.enabled:
            return None
        with self._locked():
            current_bytes = self._measure_current_bytes()
        return max(self.max_bytes - current_bytes, 0)

    def try_append_payloads(self, payloads: list[tuple[Path, bytes]]) -> bool:
        normalized = [
            (Path(path), payload)
            for path, payload in payloads
            if payload
        ]
        if not normalized:
            return True

        if not self.enabled:
            for path, payload in normalized:
                append_jsonl_payload(path, payload)
            return True

        incoming_bytes = sum(len(payload) for _, payload in normalized)
        with self._locked():
            current_bytes = self._measure_current_bytes()
            if current_bytes + incoming_bytes > self.max_bytes:
                self._log_paused(current_bytes, incoming_bytes)
                return False

            self._log_resumed(current_bytes)
            for path, payload in normalized:
                append_jsonl_payload(path, payload)
        return True

    def note_blocked(self, incoming_bytes: int) -> None:
        if not self.enabled:
            return
        with self._locked():
            current_bytes = self._measure_current_bytes()
            self._log_paused(current_bytes, incoming_bytes)

    @contextlib.contextmanager
    def _locked(self):
        ensure_dir(str(self.export_root))
        with open(self.lock_path, "a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _measure_current_bytes(self) -> int:
        total = 0
        for root, _, files in os.walk(self.export_root):
            for name in files:
                file_path = Path(root) / name
                if file_path == self.lock_path:
                    continue
                try:
                    total += file_path.stat().st_size
                except FileNotFoundError:
                    continue
        return total

    def _log_paused(self, current_bytes: int, incoming_bytes: int) -> None:
        if self._paused:
            return
        self.logger.warning(
            "[export-max-bytes] paused export_root=%s current_bytes=%d max_bytes=%d incoming_bytes=%d",
            self.export_root,
            current_bytes,
            self.max_bytes,
            incoming_bytes,
        )
        self._paused = True

    def _log_resumed(self, current_bytes: int) -> None:
        if not self._paused:
            return
        self.logger.warning(
            "[export-max-bytes] resumed export_root=%s current_bytes=%d max_bytes=%d",
            self.export_root,
            current_bytes,
            self.max_bytes,
        )
        self._paused = False
