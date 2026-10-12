"""Utilities for run event streaming and log fan-out."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict

import anyio
import redis
from redis import asyncio as async_redis
from redis.asyncio.retry import Retry as AsyncRetry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from server.storage import run_dir


logger = logging.getLogger(__name__)


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
    """Persist workflow logs and deliver best-effort, live-only events via Redis."""

    def __init__(
        self, *, buffer_size: int = 256, heartbeat_seconds: float = 15,
        redis_url: str | None = None,
    ) -> None:
        if buffer_size < 1 or heartbeat_seconds <= 0:
            raise ValueError("Event buffer size and heartbeat interval must be positive")
        self.buffer_size = buffer_size
        self.heartbeat_seconds = heartbeat_seconds
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://:redis_password@localhost:6379/0")
        self._publisher = redis.Redis.from_url(
            self.redis_url, decode_responses=True,
            socket_connect_timeout=5, socket_timeout=5,
            retry=Retry(NoBackoff(), 0),
        )
        self._database = self._publisher.connection_pool.connection_kwargs.get("db", 0)

    def _channel(self, run_id: str) -> str:
        # Redis Pub/Sub is otherwise global across logical databases.
        return f"sable:run-events:{self._database}:{run_id}"

    def append_log(self, run_id: str, event: Dict[str, Any]) -> None:
        """Persist first; a failed live notification must not fail the workflow."""

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
        payload = json.dumps(safe_event)
        log_path = run_dir(run_id) / "logs" / "logs.ndjson"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")

        try:
            self._publisher.publish(self._channel(run_id), payload)
        except RedisError:
            logger.warning("Live event publication failed for run %s; log retained", run_id)

    @asynccontextmanager
    async def subscribe(self, run_id: str):
        """Yield a bounded live-event queue; None requests disconnect/reconnect."""
        events: asyncio.Queue[Dict[str, Any] | None] = asyncio.Queue(self.buffer_size)
        client = async_redis.Redis.from_url(
            self.redis_url, decode_responses=True, max_connections=1,
            socket_connect_timeout=5, socket_timeout=5,
            retry=AsyncRetry(NoBackoff(), 0),
        )
        pubsub = client.pubsub()
        reader = None
        try:
            await pubsub.subscribe(self._channel(run_id))
            # PUBLISH can race with SUBSCRIBE unless the server has acknowledged it.
            acknowledgement = await pubsub.get_message(timeout=5)
            if not acknowledgement or acknowledgement["type"] != "subscribe":
                raise RedisError("Run event subscription was not acknowledged")
            reader = asyncio.create_task(self._receive(pubsub, events, run_id))
            yield events
        finally:
            # Starlette cancels streams on disconnect; cleanup must survive that scope.
            with anyio.CancelScope(shield=True):
                if reader is not None:
                    reader.cancel()
                    with suppress(asyncio.CancelledError):
                        await reader
                try:
                    await pubsub.aclose()
                finally:
                    await client.aclose()

    async def _receive(self, pubsub, events: asyncio.Queue, run_id: str) -> None:
        ping_pending = False
        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=self.heartbeat_seconds,
                )
                if message is None:
                    if ping_pending:
                        raise RedisError("Run event heartbeat was not acknowledged")
                    await pubsub.ping()
                    ping_pending = True
                    continue
                ping_pending = False
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                except (ValueError, TypeError):
                    continue
                if isinstance(event, dict):
                    events.put_nowait(event)
        except (RedisError, OSError, asyncio.QueueFull):
            logger.warning("Live event stream interrupted for run %s; reconnect required", run_id)
        finally:
            # Discard stale queued events on loss; never grow a slow client's buffer.
            while not events.empty():
                events.get_nowait()
            events.put_nowait(None)


# Global hub instance
run_event_hub = RunEventHub()
