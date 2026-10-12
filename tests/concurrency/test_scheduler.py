"""Public scheduler invariants against isolated PostgreSQL/Redis services.

Only publication is stubbed; reservation, admission and worker claims use SQL.
Barriers synchronize public operations, never obsolete private scheduler hooks.
"""

from collections import Counter
from datetime import datetime, timezone
import multiprocessing
import os
import queue
import sys
import threading
import time
import traceback

import pytest

from server.database import engine, get_db_context
from server.models.run import Run
import server.services.run_scheduler as scheduler_module
from server.services.run_scheduler import RunScheduler


pytestmark = pytest.mark.integration
BARRIER_TIMEOUT = 3
WORKER_TIMEOUT = 20


def _rendezvous(barrier):
    try:
        barrier.wait(timeout=BARRIER_TIMEOUT)
    except threading.BrokenBarrierError:
        # A correct scheduler may admit only one participant to this point.
        pass


def _snapshot(run_ids):
    now = datetime.now(timezone.utc)
    with get_db_context() as db:
        runs = db.query(Run).filter(Run.id.in_(run_ids)).all()
        return {
            "statuses": {run.id: run.status for run in runs},
            "active_count": db.query(Run).filter(Run.status == "running").count(),
            # There is no queued_at column; updated_at marks the queue transition.
            "queue_age_seconds": {
                run.id: max(0.0, (now - run.updated_at).total_seconds())
                for run in runs if run.status == "queued"
            },
        }


def _record(metrics, run_ids, dispatches, **extra):
    snapshot = _snapshot(run_ids)
    queue_ages = extra.pop("queue_age_seconds", {})
    queue_ages.update(snapshot["queue_age_seconds"])
    snapshot["queue_age_seconds"] = queue_ages
    metrics(
        **snapshot,
        dispatches=dispatches,
        duplicate_dispatches=sum(count - 1 for count in Counter(dispatches).values()),
        **extra,
    )
    return snapshot


def _thread_submissions(scheduler, run_ids, barrier):
    results = queue.Queue()

    def submit(run_id):
        try:
            _rendezvous(barrier)
            results.put({"run_id": run_id, "status": scheduler.submit_run(run_id)})
        except Exception:
            results.put({"run_id": run_id, "error": traceback.format_exc(limit=5)})

    workers = [threading.Thread(target=submit, args=(run_id,), daemon=True)
               for run_id in run_ids]
    started = []
    try:
        for worker in workers:
            worker.start()
            started.append(worker)
        deadline = time.monotonic() + WORKER_TIMEOUT
        for worker in started:
            worker.join(max(0.0, deadline - time.monotonic()))
    finally:
        barrier.abort()
        for worker in started:
            worker.join(timeout=1)
    reports = []
    while True:
        try:
            reports.append(results.get_nowait())
        except queue.Empty:
            break
    return reports, sum(worker.is_alive() for worker in started)


def _process_scheduler(mode, run_id, barrier, connection):
    # Never let a forked scheduler use the parent's PostgreSQL connections.
    engine.dispose(close=False)
    dispatches = []
    report = {"pid": os.getpid(), "dispatches": dispatches}
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(scheduler_module, "start_run", lambda run_id, token: dispatches.append(run_id))
            scheduler = RunScheduler()
            _rendezvous(barrier)
            if mode == "drain":
                scheduler.reconcile()
            else:
                report["status"] = scheduler.submit_run(run_id)
    except Exception:
        report["error"] = traceback.format_exc(limit=5)
    finally:
        engine.dispose()
        try:
            connection.send(report)
        finally:
            connection.close()


def _process_schedulers(mode, run_ids):
    if sys.platform != "linux":
        pytest.skip("Independent-process regression requires the Linux compose suite")
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    workers = []
    reports = []
    try:
        for run_id in run_ids:
            receiver, sender = context.Pipe(duplex=False)
            worker = context.Process(
                target=_process_scheduler, args=(mode, run_id, barrier, sender),
                daemon=True,
            )
            workers.append((worker, receiver, sender))
            worker.start()
            sender.close()
        deadline = time.monotonic() + WORKER_TIMEOUT
        for worker, receiver, _ in workers:
            if receiver.poll(max(0.0, deadline - time.monotonic())):
                try:
                    reports.append(receiver.recv())
                except EOFError:
                    reports.append({"pid": worker.pid, "error": "Worker exited without a report"})
            else:
                reports.append({"pid": worker.pid, "error": "Worker timed out"})
            worker.join(max(0.0, deadline - time.monotonic()))
            if worker.is_alive() or worker.exitcode != 0:
                reports[-1]["error"] = "Worker did not exit cleanly"
    finally:
        barrier.abort()
        for worker, receiver, sender in workers:
            if worker.pid is not None:
                if worker.is_alive():
                    worker.terminate()
                worker.join(timeout=2)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=2)
                if not worker.is_alive():
                    worker.close()
            receiver.close()
            sender.close()
    return reports


def test_global_five_limit_allows_one_user_to_fill_all_slots(
    two_users, make_run, monkeypatch, metrics,
):
    dispatched = []
    monkeypatch.setattr(scheduler_module, "start_run", lambda run_id, token: dispatched.append(run_id))
    scheduler = RunScheduler(max_concurrent=5)
    first = [make_run(two_users[0]) for _ in range(5)]
    other = make_run(two_users[1])
    ids = [run.id for run in first] + [other.id]
    outcomes = [scheduler.submit_run(run_id) for run_id in ids]
    observed = _record(metrics, ids, dispatched, admission_results=outcomes)
    assert observed["active_count"] == 0
    assert outcomes == ["queued"] * 6
    assert set(observed["statuses"].values()) == {"queued"}
    assert Counter(dispatched) == Counter(run.id for run in first)
    assert dispatch_states(ids) == {**{run.id: "dispatched" for run in first}, other.id: "pending"}


def dispatch_states(run_ids):
    from server.models.run_dispatch import RunDispatch

    with get_db_context() as db:
        return {row.run_id: row.state for row in db.query(RunDispatch).filter(RunDispatch.run_id.in_(run_ids))}


def test_same_instance_concurrent_admission_respects_cap(two_users, make_run, monkeypatch, metrics):
    dispatched = []
    monkeypatch.setattr(scheduler_module, "start_run", lambda run_id, token: dispatched.append(run_id))
    scheduler = RunScheduler(max_concurrent=1)
    ids = [make_run(user).id for user in two_users]
    reports, unfinished = _thread_submissions(scheduler, ids, threading.Barrier(2))
    observed = _record(metrics, ids, dispatched, worker_reports=reports, unfinished_workers=unfinished)
    assert unfinished == 0
    assert len(reports) == 2 and not any("error" in report for report in reports), reports
    assert observed["active_count"] == 0
    assert set(observed["statuses"].values()) == {"queued"}
    assert {report["status"] for report in reports} == {"queued"}
    assert sorted(dispatch_states(ids).values()) == ["dispatched", "pending"]
    assert len(dispatched) == 1


def test_independent_processes_dispatch_queued_run_only_once(two_users, make_run, metrics):
    RunScheduler(max_concurrent=1)
    queued = make_run(two_users[0], status="queued")
    reports = _process_schedulers("drain", [queued.id, queued.id])
    dispatched = [run_id for report in reports for run_id in report.get("dispatches", [])]
    observed = _record(metrics, [queued.id], dispatched, worker_reports=reports)
    assert len(reports) == 2 and not any("error" in report for report in reports), reports
    assert len({report["pid"] for report in reports}) == 2
    assert dispatched == [queued.id]
    assert observed["active_count"] == 0
    assert observed["statuses"][queued.id] == "queued"
    assert dispatch_states([queued.id]) == {queued.id: "dispatched"}


def test_independent_process_admissions_share_one_global_cap(two_users, make_run, metrics):
    RunScheduler(max_concurrent=1)
    ids = [make_run(user).id for user in two_users]
    reports = _process_schedulers("submit", ids)
    dispatched = [run_id for report in reports for run_id in report.get("dispatches", [])]
    observed = _record(metrics, ids, dispatched, worker_reports=reports)
    assert len(reports) == 2 and not any("error" in report for report in reports), reports
    assert len({report["pid"] for report in reports}) == 2
    assert observed["active_count"] == 0
    assert set(observed["statuses"].values()) == {"queued"}
    assert {report["status"] for report in reports} == {"queued"}
    assert sorted(dispatch_states(ids).values()) == ["dispatched", "pending"]
    assert len(dispatched) == 1


def test_broker_failure_releases_reservation_and_retries_automatically(two_users, make_run, monkeypatch):
    from datetime import timedelta
    from server.models.run_dispatch import RunDispatch

    scheduler = RunScheduler(max_concurrent=1)
    failed, healthy = [make_run(user) for user in two_users]
    attempts, dispatched = [], []

    def dispatch(run_id, token):
        attempts.append((run_id, token))
        if run_id == failed.id:
            raise ConnectionError("Simulated broker dispatch failure")
        dispatched.append((run_id, token))

    monkeypatch.setattr(scheduler_module, "start_run", dispatch)
    assert scheduler.submit_run(failed.id) == "queued"
    assert dispatch_states([failed.id]) == {failed.id: "pending"}
    assert scheduler.submit_run(healthy.id) == "queued"
    assert [run_id for run_id, _ in dispatched] == [healthy.id]
    from server.services.run_execution import claim_run
    lease = claim_run(*dispatched[0])
    assert lease is not None and lease.finish("completed")
    monkeypatch.setattr(scheduler_module, "start_run", lambda run_id, token: dispatched.append((run_id, token)))
    with get_db_context() as db, db.begin():
        db.get(RunDispatch, failed.id).next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    scheduler.reconcile()
    assert [run_id for run_id, _ in dispatched] == [healthy.id, failed.id]
    assert dispatched[-1][1] != attempts[0][1]
    assert dispatch_states([failed.id]) == {failed.id: "dispatched"}


def test_global_policy_persists_across_default_constructors(two_users, make_run, monkeypatch):
    dispatched = []
    monkeypatch.setattr(scheduler_module, "start_run", lambda run_id, token: dispatched.append(run_id))
    scheduler = RunScheduler(max_concurrent=1)
    other = RunScheduler()
    ids = [make_run(two_users[0]).id for _ in range(3)]
    for run_id in ids:
        other.submit_run(run_id)
    assert len(dispatched) == 1
    scheduler.set_max_concurrent(2)
    other.reconcile()
    assert dispatched == ids[:2]
    RunScheduler(max_concurrent=3)
    other.reconcile()
    assert dispatched == ids


def test_default_constructor_does_not_access_database_or_reconcile(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Default constructor must not access the database")

    monkeypatch.setattr(scheduler_module, "get_db_context", forbidden)
    monkeypatch.setattr(RunScheduler, "reconcile", forbidden)
    RunScheduler()


def test_fifo_queue_drains_after_worker_finishes(two_users, make_run, monkeypatch):
    from server.services.run_execution import claim_run

    messages = []
    monkeypatch.setattr(scheduler_module, "start_run", lambda run_id, token: messages.append((run_id, token)))
    scheduler = RunScheduler(max_concurrent=1)
    ids = [make_run(two_users[i % 2]).id for i in range(4)]
    for run_id in ids:
        scheduler.submit_run(run_id)
    for index, run_id in enumerate(ids):
        assert [item[0] for item in messages] == ids[:index + 1]
        lease = claim_run(*messages[index])
        assert lease is not None and lease.finish("completed")
        scheduler.on_run_finished(run_id)
    assert _snapshot(ids)["statuses"] == dict.fromkeys(ids, "completed")


def test_database_failure_is_fail_closed(two_users, make_run, monkeypatch):
    from sqlalchemy.exc import OperationalError

    run = make_run(two_users[0])
    messages = []
    scheduler = RunScheduler(max_concurrent=1)

    def unavailable():
        raise OperationalError("controlled unavailable database", {}, None)

    monkeypatch.setattr(scheduler_module, "start_run", lambda *args: messages.append(args))
    monkeypatch.setattr(scheduler_module, "get_db_context", unavailable)
    with pytest.raises(OperationalError):
        scheduler.submit_run(run.id)
    with pytest.raises(OperationalError):
        scheduler.reconcile()
    assert messages == []


def test_slow_publication_does_not_hold_admission_lock(two_users, make_run, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    entered, release = threading.Event(), threading.Event()
    first, second = [make_run(user) for user in two_users]
    messages = []

    def publish(run_id, token):
        messages.append((run_id, token))
        if run_id == first.id:
            entered.set()
            assert release.wait(5)

    monkeypatch.setattr(scheduler_module, "start_run", publish)
    scheduler = RunScheduler(max_concurrent=2)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(scheduler.submit_run, first.id)
        try:
            assert entered.wait(3)
            assert pool.submit(RunScheduler().submit_run, second.id).result(timeout=2) == "queued"
            assert len(messages) == 2
        finally:
            release.set()
        assert pending.result(timeout=3) == "queued"
