"""Scheduler enforcing concurrent run limits."""

from __future__ import annotations

import os
from threading import RLock
from typing import Optional

from server.database import get_db_context
from server.services.cache_service import cache_service
from server.services.run_launcher import start_run
from server.services.run_service import run_service
from server.storage import results_json_path, summary_txt_path


DEFAULT_MAX_CONCURRENT = 5
ACTIVE_STATUSES = {"running"}


class RunScheduler:
    """Coordinate run execution to respect concurrency limits."""

    def __init__(self, max_concurrent: Optional[int] = None) -> None:
        self._lock = RLock()
        self._max_concurrent = self._resolve_max_concurrent(max_concurrent)
        # Attempt to resume any queued runs on startup
        self._try_schedule_next_locked()

    def submit_run(self, run_id: str) -> str:
        """Submit a run for execution.

        Returns the resulting status ("running" or "queued").
        """
        with self._lock:
            if self._current_active_runs() >= self._max_concurrent:
                self._mark_as_queued(run_id)
                return "queued"

        # Start outside lock to avoid holding it while launching thread
        self._start_run(run_id)
        return "running"

    def on_run_finished(self, run_id: str) -> None:
        """Signal that capacity may have opened up."""
        with self._lock:
            self._try_schedule_next_locked()

    def set_max_concurrent(self, value: int) -> None:
        with self._lock:
            self._max_concurrent = max(1, int(value))
            self._try_schedule_next_locked()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _mark_as_queued(self, run_id: str) -> None:
        with get_db_context() as db:
            run_model = run_service.update_run_status(db, run_id, "queued")
            if run_model:
                self._refresh_cache(run_model)

    def _start_run(self, run_id: str) -> None:
        with get_db_context() as db:
            run_model = run_service.update_run_status(db, run_id, "running")
            if run_model:
                self._refresh_cache(run_model)
        start_run(run_id)

    def _refresh_cache(self, run_model) -> None:
        metadata = run_model.extra_metadata or {}
        paths = metadata.get("paths", {}) if isinstance(metadata, dict) else {}
        summary_exists = summary_txt_path(run_model.id).exists() if paths else False
        results_exists = results_json_path(run_model.id).exists() if paths else False

        info = run_service.run_to_info(
            run_model,
            summary_available=summary_exists,
            results_available=results_exists,
            paths=paths,
        )
        cache_service.cache_run(run_model.id, info.model_dump())
        cache_service.invalidate_user_runs_list(str(run_model.user_id))

    def _try_schedule_next_locked(self) -> None:
        # Fill available slots up to the concurrency limit
        while True:
            if self._current_active_runs() >= self._max_concurrent:
                return
            next_run_id = self._next_queued_run_id()
            if not next_run_id:
                return
            self._start_run(next_run_id)

    def _current_active_runs(self) -> int:
        with get_db_context() as db:
            return run_service.count_runs_with_status(db, ACTIVE_STATUSES)

    def _next_queued_run_id(self) -> Optional[str]:
        with get_db_context() as db:
            run_model = run_service.get_next_queued_run(db)
            return run_model.id if run_model else None

    def _resolve_max_concurrent(self, override: Optional[int]) -> int:
        if override is not None:
            return max(1, int(override))
        env_value = os.getenv("MAX_CONCURRENT_RUNS") or os.getenv("LIZARD_MAX_CONCURRENT_RUNS")
        if not env_value:
            return DEFAULT_MAX_CONCURRENT
        try:
            return max(1, int(env_value))
        except ValueError:
            return DEFAULT_MAX_CONCURRENT


# Global scheduler instance
run_scheduler = RunScheduler()
