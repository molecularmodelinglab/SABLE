"""Read-only queue explanations for runs already authorized by the caller."""

from sqlalchemy import func
from sqlalchemy.orm import Session

from server.models.run_dispatch import RunDispatch
from server.schemas.run import RunInfo


def add_queue_details(db: Session, infos: list[RunInfo]) -> None:
    queued = {}
    for info in infos:
        info.queue_reason = None
        info.queue_retry_at = None
        if info.status == "queued":
            info.queue_reason = "waiting_for_admission"
            queued[info.id] = info
    if not queued:
        return

    rows = db.query(
        RunDispatch.run_id, RunDispatch.state, RunDispatch.next_attempt_at,
        func.clock_timestamp().label("observed_at"),
    ).filter(RunDispatch.run_id.in_(queued)).all()
    for row in rows:
        info = queued[row.run_id]
        if row.state == "reserved":
            info.queue_reason = "dispatching"
        elif row.state == "dispatched":
            info.queue_reason = "waiting_for_worker"
        elif row.state == "pending" and row.next_attempt_at > row.observed_at:
            info.queue_reason = "dispatch_retry"
            info.queue_retry_at = row.next_attempt_at
        elif row.state != "pending":
            info.queue_reason = None
