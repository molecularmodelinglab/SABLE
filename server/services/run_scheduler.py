"""PostgreSQL-serialized admission with a recoverable publication outbox.

Only a worker claim changes public QUEUED to RUNNING. Database failures propagate:
there is deliberately no process-local capacity or missing-schema fallback.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert

from server.database import get_db_context
from server.models.run import Run
from server.models.run_dispatch import RunDispatch, SchedulerState


logger = logging.getLogger(__name__)
DEFAULT_MAX_CONCURRENT = 5
RESERVATION_SECONDS = 300
PUBLICATION_LEASE_SECONDS = 30
RECONCILE_SECONDS = 5
RETRY_MAX_SECONDS = 60
SLOT_STATES = {"reserved", "dispatched", "running"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "canceled", "stopped"}


def start_run(run_id: str, dispatch_token: str) -> None:
    # Avoid importing workflow/storage infrastructure during scheduler bootstrap.
    from server.services.run_launcher import start_run as publish
    publish(run_id, dispatch_token)


def configure_transaction(db) -> None:
    """Bound DB lock/statement waits and abandoned transactions, only for this unit of work."""
    db.execute(text(
        "SELECT set_config('statement_timeout', '10000', true), "
        "set_config('lock_timeout', '5000', true), "
        "set_config('idle_in_transaction_session_timeout', '15000', true)"
    ))


def database_now(db):
    """Read wall time after taking locks, not PostgreSQL's transaction start time."""
    return db.query(func.clock_timestamp()).scalar()


def invalidate_run_cache(run_id: str, user_id: str) -> None:
    """Invalidate only; writing detached snapshots here can overwrite final status."""
    try:
        from server.services.cache_service import cache_service
        cache_service.invalidate_run(run_id)
        cache_service.invalidate_user_runs_list(user_id)
        if cache_service.is_connected():
            cache_service.redis.delete(f"run:status:{run_id}")
    except Exception:
        logger.warning("Unable to invalidate run cache for %s", run_id, exc_info=True)


def _clear_publication(dispatch) -> None:
    dispatch.publication_owner = None
    dispatch.publication_lease_expires_at = None


def _release_reservation(dispatch, now) -> None:
    dispatch.state = "pending"
    dispatch.dispatch_token = None
    dispatch.reservation_deadline = None
    dispatch.updated_at = now
    _clear_publication(dispatch)


class RunScheduler:
    """Share one persistent limit across all web/dispatcher processes."""

    def __init__(self, max_concurrent: int | None = None) -> None:
        if max_concurrent is not None:
            self._persist_limit(max_concurrent)

    @staticmethod
    def _resolve_max_concurrent(override: int | None = None) -> int:
        if override is not None:
            return max(1, int(override))
        value = os.getenv("MAX_CONCURRENT_RUNS") or os.getenv("SABLE_MAX_CONCURRENT_RUNS")
        try:
            return max(1, int(value)) if value else DEFAULT_MAX_CONCURRENT
        except ValueError:
            return DEFAULT_MAX_CONCURRENT

    def _lock_state(self, db):
        configure_transaction(db)
        db.execute(insert(SchedulerState).values(
            id=1, max_concurrent=self._resolve_max_concurrent(),
        ).on_conflict_do_nothing(index_elements=["id"]))
        return db.query(SchedulerState).filter_by(id=1).with_for_update().one()

    def _persist_limit(self, value: int) -> None:
        limit = self._resolve_max_concurrent(value)
        with get_db_context() as db, db.begin():
            state = self._lock_state(db)
            state.max_concurrent = limit
            state.updated_at = database_now(db)

    def set_max_concurrent(self, value: int) -> None:
        self._persist_limit(value)
        self.reconcile()

    def submit_run(self, run_id: str) -> str:
        """Atomically enqueue an already prepared Run and its durable outbox row."""
        with get_db_context() as db, db.begin():
            self._lock_state(db)
            dispatch = db.query(RunDispatch).filter_by(run_id=run_id).with_for_update().first()
            run = db.query(Run).filter_by(id=run_id).with_for_update().one()
            if run.status not in {"pending", "queued"}:
                return run.status
            if dispatch is not None and dispatch.state in {"finished", "failed"}:
                return run.status
            now = database_now(db)
            run.status = "queued"
            run.updated_at = now
            if dispatch is None:
                db.add(RunDispatch(run_id=run_id, state="pending", next_attempt_at=now))
            user_id = str(run.user_id)
        invalidate_run_cache(run_id, user_id)
        self.reconcile()
        # Publication is not execution, even when a broker acknowledges it.
        return "queued"

    def on_run_finished(self, run_id: str) -> None:
        self.reconcile()

    def reconcile(self) -> None:
        """Recover leases and reserve FIFO capacity, then publish without DB locks."""
        publications = []
        invalidations = []
        with get_db_context() as db, db.begin():
            state = self._lock_state(db)
            # All state writers lock dispatch before Run. Holding these locks makes
            # admission observe worker claims/finishes as one consistent snapshot.
            dispatches = db.query(RunDispatch).filter(
                RunDispatch.state.in_(["pending", "reserved", "dispatched", "running"]),
            ).order_by(RunDispatch.run_id).with_for_update().all()
            runs = {}
            for dispatch in dispatches:
                run = db.query(Run).filter_by(id=dispatch.run_id).with_for_update().first()
                if run is not None:
                    runs[run.id] = run
            now = database_now(db)
            for dispatch in dispatches:
                run = runs.get(dispatch.run_id)
                if run is None:
                    continue
                if run.status in TERMINAL_STATUSES:
                    dispatch.state = "failed" if run.status == "failed" else "finished"
                    dispatch.lease_expires_at = None
                    dispatch.reservation_deadline = None
                    _clear_publication(dispatch)
                elif dispatch.state == "running" and (
                    dispatch.lease_expires_at is None or dispatch.lease_expires_at <= now
                ):
                    dispatch.state = "failed"
                    dispatch.lease_expires_at = None
                    dispatch.updated_at = now
                    _clear_publication(dispatch)
                    run.status = "failed"
                    run.exit_reason = "Execution lease expired; submit a new run to retry."
                    run.updated_at = now
                    invalidations.append((run.id, str(run.user_id)))
                elif dispatch.state in {"reserved", "dispatched"} and (
                    dispatch.reservation_deadline is None or dispatch.reservation_deadline <= now
                ):
                    _release_reservation(dispatch, now)
                    dispatch.next_attempt_at = now
            db.flush()

            # Old queued rows are safe to adopt. Old RUNNING rows have no lease;
            # count them indefinitely rather than guessing when their work ended.
            legacy = db.query(Run).outerjoin(RunDispatch, RunDispatch.run_id == Run.id).filter(
                RunDispatch.run_id.is_(None), Run.status.in_(["queued", "running"]),
            ).order_by(Run.created_at, Run.id).with_for_update(of=Run).all()
            legacy_running = 0
            for run in legacy:
                if run.status == "running":
                    legacy_running += 1
                else:
                    dispatch = RunDispatch(run_id=run.id, state="pending", next_attempt_at=now,
                                           publish_attempts=0)
                    db.add(dispatch)
                    dispatches.append(dispatch)
                    runs[run.id] = run

            active = legacy_running + sum(d.state in SLOT_STATES for d in dispatches)
            available = max(0, state.max_concurrent - active)
            pending = sorted(
                (d for d in dispatches if d.state == "pending" and d.run_id in runs
                 and runs[d.run_id].status == "queued" and d.next_attempt_at <= now),
                key=lambda d: (runs[d.run_id].created_at, d.run_id),
            )
            for dispatch in pending[:available]:
                dispatch.state = "reserved"
                dispatch.dispatch_token = str(uuid4())
                dispatch.reservation_deadline = now + timedelta(seconds=RESERVATION_SECONDS)
                dispatch.updated_at = now

            for dispatch in dispatches:
                if dispatch.state != "reserved" or dispatch.next_attempt_at > now:
                    continue
                if (dispatch.publication_lease_expires_at is not None
                        and dispatch.publication_lease_expires_at > now):
                    continue
                owner = str(uuid4())
                dispatch.publication_owner = owner
                dispatch.publication_lease_expires_at = now + timedelta(seconds=PUBLICATION_LEASE_SECONDS)
                dispatch.publish_attempts += 1
                dispatch.updated_at = now
                publications.append((dispatch.run_id, dispatch.dispatch_token, owner))
        for run_id, user_id in invalidations:
            invalidate_run_cache(run_id, user_id)
        for run_id, token, owner in publications:
            self._publish(run_id, token, owner)

    def _publish(self, run_id: str, token: str, owner: str) -> None:
        failed = False
        try:
            start_run(run_id, token)
        except Exception:
            failed = True
            logger.warning("Broker publication failed for run %s; retry is durable", run_id, exc_info=True)
        with get_db_context() as db, db.begin():
            configure_transaction(db)
            dispatch = db.query(RunDispatch).filter_by(run_id=run_id).with_for_update().first()
            # An ACK/error can arrive after a worker claim or after another
            # generation was reserved. Neither may be undone by this publisher.
            if (dispatch is None or dispatch.state != "reserved"
                    or dispatch.dispatch_token != token or dispatch.publication_owner != owner):
                return
            now = database_now(db)
            if failed:
                _release_reservation(dispatch, now)
                delay = min(RETRY_MAX_SECONDS, 2 ** min(dispatch.publish_attempts, 6))
                dispatch.next_attempt_at = now + timedelta(seconds=delay)
            else:
                dispatch.state = "dispatched"
                dispatch.updated_at = now
                _clear_publication(dispatch)


run_scheduler = RunScheduler()
