"""Utilities for run event streaming and log fan-out."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List

from server.storage import run_dir


Subscriber = Callable[[Dict[str, Any]], None]


def _make_json_safe(value: Any) -> Any:
    """Recursively convert values into JSON-serializable primitives."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return _make_json_safe(value.model_dump())
        except Exception:
            return str(value)

    if isinstance(value, dict):
        safe_dict: Dict[str, Any] = {}
        for key, item in value.items():
            safe_key = str(key)
            safe_dict[safe_key] = _make_json_safe(item)
        return safe_dict

    if isinstance(value, (list, tuple, set, frozenset)):
        return [_make_json_safe(item) for item in value]

    # Fallback to string representation for unknown types
    return str(value)


class RunEventHub:
    """Manages run event subscriptions and log fan-out."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Subscriber]] = {}
        self._lock = RLock()

    def append_log(self, run_id: str, event: Dict[str, Any]) -> None:
        """Persist a log event and notify subscribers with JSON-safe payloads."""

        try:
            safe_event = _make_json_safe(event)
        except Exception as exc:  # pragma: no cover - defensive guard
            safe_event = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "WARNING",
                "message": "Failed to serialize workflow event; falling back to string",
                "original": repr(event),
                "error": str(exc),
            }
        log_path = run_dir(run_id) / "logs" / "logs.ndjson"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe_event) + "\n")

        for callback in self._get_subscribers(run_id):
            try:
                callback(safe_event)
            except Exception:
                # Fan-out should never break due to subscriber errors
                continue

    def subscribe(self, run_id: str, callback: Subscriber) -> None:
        with self._lock:
            self._subscribers.setdefault(run_id, []).append(callback)

    def unsubscribe(self, run_id: str, callback: Subscriber) -> None:
        with self._lock:
            callbacks = self._subscribers.get(run_id)
            if not callbacks:
                return
            try:
                callbacks.remove(callback)
            except ValueError:
                return
            if not callbacks:
                self._subscribers.pop(run_id, None)

    def _get_subscribers(self, run_id: str) -> List[Subscriber]:
        with self._lock:
            return list(self._subscribers.get(run_id, ()))


# Global hub instance
run_event_hub = RunEventHub()
