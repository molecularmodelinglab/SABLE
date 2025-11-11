"""Utilities for run event streaming and log fan-out."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List

from server.storage import run_dir


Subscriber = Callable[[Dict[str, Any]], None]


class RunEventHub:
    """Manages run event subscriptions and log persistence."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Subscriber]] = {}
        self._lock = RLock()

    def append_log(self, run_id: str, event: Dict[str, Any]) -> None:
        """Persist a log event and notify subscribers."""
        log_path = run_dir(run_id) / "logs" / "logs.ndjson"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")

        for callback in self._get_subscribers(run_id):
            try:
                callback(event)
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
