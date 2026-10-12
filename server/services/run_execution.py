"""Token-fenced, independently heartbeating execution leases.

Lease loss cannot cancel an in-flight external call. Abandoned executions are
failed by reconciliation, never automatically executed again.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func

from server.database import get_db_context
from server.models.run import Run
from server.models.run_dispatch import RunDispatch
from server.services.run_scheduler import TERMINAL_STATUSES, configure_transaction, invalidate_run_cache


logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 15
EXECUTION_LEASE_SECONDS = 90


class LeaseLost(RuntimeError):
    """This process no longer has authority to mutate or continue this run."""


def claim_run(run_id: str, dispatch_token: str) -> RunLease | None:
    """Claim an unstarted, unexpired generation once (not once per Celery ID)."""
    if not dispatch_token:
        return None
    owner = str(uuid4())
    started = time.monotonic()
    with get_db_context() as db, db.begin():
        configure_transaction(db)
        dispatch = db.query(RunDispatch).filter_by(run_id=run_id).with_for_update().first()
        if dispatch is None:
            return None
        run = db.query(Run).filter_by(id=run_id).with_for_update().first()
        if run is None or run.status != "queued":
            return None
        changed = db.query(RunDispatch).filter(
            RunDispatch.run_id == run_id,
            RunDispatch.dispatch_token == dispatch_token,
            RunDispatch.state.in_(["reserved", "dispatched"]),
            RunDispatch.reservation_deadline > func.clock_timestamp(),
        ).update({
            RunDispatch.state: "running",
            RunDispatch.execution_owner: owner,
            RunDispatch.heartbeat_at: func.clock_timestamp(),
            RunDispatch.lease_expires_at: func.clock_timestamp() + timedelta(seconds=EXECUTION_LEASE_SECONDS),
            RunDispatch.publication_owner: None,
            RunDispatch.publication_lease_expires_at: None,
            RunDispatch.updated_at: func.clock_timestamp(),
        }, synchronize_session=False)
        if not changed:
            return None
        run.status = "running"
        run.updated_at = func.clock_timestamp()
        user_id = str(run.user_id)
    lease = RunLease(run_id, dispatch_token, owner, user_id, started + EXECUTION_LEASE_SECONDS)
    invalidate_run_cache(run_id, user_id)
    return lease


class RunLease:
    """Execution authority; all durable mutations are conditional on live ownership."""

    def __init__(self, run_id: str, dispatch_token: str, owner: str,
                 user_id: str, deadline: float) -> None:
        self.run_id = run_id
        self.dispatch_token = dispatch_token
        self.owner = owner
        self.user_id = user_id
        self._deadline = deadline
        self._lost = False
        self._finished = False
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _mark_lost(self) -> None:
        with self._state_lock:
            self._lost = True
        self._stop.set()

    def check(self) -> None:
        """Fail closed even if the heartbeat thread is blocked in a DB call."""
        with self._state_lock:
            if self._lost or self._finished or time.monotonic() >= self._deadline:
                self._lost = True
                self._stop.set()
                raise LeaseLost(f"Execution lease lost for {self.run_id}")

    def _owned(self, db):
        return db.query(RunDispatch).filter(
            RunDispatch.run_id == self.run_id,
            RunDispatch.dispatch_token == self.dispatch_token,
            RunDispatch.execution_owner == self.owner,
            RunDispatch.state == "running",
            RunDispatch.lease_expires_at > func.clock_timestamp(),
        )

    def _lock_run(self, db):
        configure_transaction(db)
        dispatch = db.query(RunDispatch).filter_by(run_id=self.run_id).with_for_update().first()
        if dispatch is None:
            return None
        return db.query(Run).filter_by(id=self.run_id).with_for_update().first()

    def renew(self) -> bool:
        """Renew only a still-live lease. A failed/uncertain renewal is permanent."""
        try:
            self.check()
            started = time.monotonic()
            with get_db_context() as db, db.begin():
                run = self._lock_run(db)
                self.check()
                if run is None or run.status != "running":
                    self._mark_lost()
                    return False
                changed = self._owned(db).update({
                    RunDispatch.heartbeat_at: func.clock_timestamp(),
                    RunDispatch.lease_expires_at: func.clock_timestamp() + timedelta(seconds=EXECUTION_LEASE_SECONDS),
                    RunDispatch.updated_at: func.clock_timestamp(),
                }, synchronize_session=False)
                if not changed:
                    self._mark_lost()
                    return False
            with self._state_lock:
                if self._lost or self._finished or time.monotonic() >= self._deadline:
                    self._lost = True
                    self._stop.set()
                    return False
                self._deadline = started + EXECUTION_LEASE_SECONDS
            return True
        except Exception:
            self._mark_lost()
            logger.warning("Execution lease renewal failed for %s", self.run_id, exc_info=True)
            return False

    def update_molecules(self, molecules: list) -> bool:
        """Store workflow output only while this owner still has authority."""
        try:
            self.check()
            with get_db_context() as db, db.begin():
                run = self._lock_run(db)
                self.check()
                if run is None or run.status != "running" or not self._owned(db).update(
                    {RunDispatch.updated_at: func.clock_timestamp()}, synchronize_session=False,
                ):
                    self._mark_lost()
                    return False
                run.starting_molecules = list(molecules)
                run.updated_at = func.clock_timestamp()
            invalidate_run_cache(self.run_id, self.user_id)
            return True
        except Exception:
            self._mark_lost()
            logger.warning("Execution output update failed for %s", self.run_id, exc_info=True)
            return False

    def finish(self, status: str, exit_reason: str | None = None,
               starting_molecules: list | None = None) -> bool:
        """Atomically finish the Run and dispatch, never a successor/expired owner."""
        if status not in TERMINAL_STATUSES:
            raise ValueError("Execution finish requires a terminal status")
        try:
            self.check()
            with get_db_context() as db, db.begin():
                run = self._lock_run(db)
                self.check()
                if run is None or run.status != "running" or not self._owned(db).update({
                    RunDispatch.state: "failed" if status == "failed" else "finished",
                    RunDispatch.lease_expires_at: None,
                    RunDispatch.reservation_deadline: None,
                    RunDispatch.publication_owner: None,
                    RunDispatch.publication_lease_expires_at: None,
                    RunDispatch.updated_at: func.clock_timestamp(),
                }, synchronize_session=False):
                    self._mark_lost()
                    return False
                run.status = status
                run.exit_reason = exit_reason
                run.updated_at = func.clock_timestamp()
                if starting_molecules is not None:
                    run.starting_molecules = list(starting_molecules)
            with self._state_lock:
                self._finished = True
            self._stop.set()
            invalidate_run_cache(self.run_id, self.user_id)
            return True
        except Exception:
            self._mark_lost()
            logger.warning("Execution finish failed for %s", self.run_id, exc_info=True)
            return False

    def _heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            if not self.renew():
                return

    def __enter__(self) -> RunLease:
        self.check()
        if self._thread is not None:
            raise RuntimeError("RunLease context cannot be entered twice")
        self._thread = threading.Thread(
            target=self._heartbeat, name=f"run-heartbeat-{self.run_id}", daemon=True,
        )
        try:
            self._thread.start()
        except Exception:
            self._stop.set()
            self.finish("failed", exit_reason="Execution heartbeat thread could not start.")
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._stop.set()
        with self._state_lock:
            if not self._finished:
                self._lost = True
        if self._thread is not None:
            # A DB driver can block indefinitely; never block workflow shutdown on it.
            self._thread.join(timeout=1.0)
