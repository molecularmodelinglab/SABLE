"""Scheduler invariants against the isolated PostgreSQL/Redis compose services.

Only the broker-dispatch start_run boundary is stubbed, avoiding Celery's
thread-local task proxy. Real scheduler status updates and cache writes still
run. Barriers expire gracefully if corrected scheduling serializes admission.
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
            patch.setattr(scheduler_module, "start_run", dispatches.append)
            if mode == "drain":
                original_read = RunScheduler._next_queued_run_id
                synchronized = False

                def read_queued(scheduler):
                    nonlocal synchronized
                    candidate = original_read(scheduler)
                    if candidate is not None and not synchronized:
                        synchronized = True
                        _rendezvous(barrier)
                    return candidate

                patch.setattr(RunScheduler, "_next_queued_run_id", read_queued)
                # Startup itself drains queued runs, exactly as an API process does.
                RunScheduler(max_concurrent=1)
            else:
                scheduler = RunScheduler(max_concurrent=1)
                original_start = scheduler._start_run

                def start(candidate):
                    _rendezvous(barrier)
                    return original_start(candidate)

                patch.setattr(scheduler, "_start_run", start)
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
    """Global capacity is policy; this does not demand per-user fairness."""
    dispatched = []
    monkeypatch.setattr(scheduler_module, "start_run", dispatched.append)
    scheduler = RunScheduler(max_concurrent=5)
    first_user_runs = [make_run(two_users[0]) for _ in range(5)]
    other_user_run = make_run(two_users[1])
    outcomes = [scheduler.submit_run(run.id) for run in first_user_runs]
    other_outcome = scheduler.submit_run(other_user_run.id)
    ids = [run.id for run in first_user_runs] + [other_user_run.id]

    observed = _record(metrics, ids, dispatched, admission_results=outcomes + [other_outcome])
    assert observed["active_count"] == 5
    assert outcomes == ["running"] * 5
    assert other_outcome == "queued"
    assert observed["statuses"] == {
        **{run.id: "running" for run in first_user_runs}, other_user_run.id: "queued",
    }
    assert Counter(dispatched) == Counter(run.id for run in first_user_runs)


def test_same_instance_concurrent_admission_respects_cap(
    two_users, make_run, monkeypatch, metrics,
):
    dispatched = []
    monkeypatch.setattr(scheduler_module, "start_run", dispatched.append)
    scheduler = RunScheduler(max_concurrent=1)
    ids = [make_run(user).id for user in two_users]
    barrier = threading.Barrier(2)
    original_start = scheduler._start_run

    def start(run_id):
        _rendezvous(barrier)
        return original_start(run_id)

    monkeypatch.setattr(scheduler, "_start_run", start)
    reports, unfinished = _thread_submissions(scheduler, ids, barrier)
    observed = _record(metrics, ids, dispatched, worker_reports=reports, unfinished_workers=unfinished)
    assert unfinished == 0, "Submission workers did not finish"
    assert len(reports) == 2 and not any("error" in report for report in reports), reports
    assert observed["active_count"] == 1, "Concurrent admissions exceeded the configured cap"
    assert sorted(observed["statuses"].values()) == ["queued", "running"]
    assert sorted(report["status"] for report in reports) == ["queued", "running"]
    assert len(dispatched) == 1


def test_independent_processes_dispatch_queued_run_only_once(two_users, make_run, metrics):
    queued = make_run(two_users[0], status="queued")
    queue_ages = _snapshot([queued.id])["queue_age_seconds"]
    reports = _process_schedulers("drain", [queued.id, queued.id])
    dispatched = [run_id for report in reports for run_id in report.get("dispatches", [])]
    observed = _record(
        metrics, [queued.id], dispatched, worker_reports=reports, queue_age_seconds=queue_ages,
    )
    assert len(reports) == 2 and not any("error" in report for report in reports), reports
    assert len({report["pid"] for report in reports}) == 2
    assert dispatched == [queued.id], "Independent schedulers dispatched the same queued run twice"
    assert observed["active_count"] == 1
    assert observed["statuses"][queued.id] == "running"


def test_independent_process_admissions_share_one_global_cap(two_users, make_run, metrics):
    ids = [make_run(user).id for user in two_users]
    reports = _process_schedulers("submit", ids)
    dispatched = [run_id for report in reports for run_id in report.get("dispatches", [])]
    observed = _record(metrics, ids, dispatched, worker_reports=reports)
    assert len(reports) == 2 and not any("error" in report for report in reports), reports
    assert len({report["pid"] for report in reports}) == 2
    assert observed["active_count"] == 1, "Process-local locks overadmitted across API processes"
    assert sorted(observed["statuses"].values()) == ["queued", "running"]
    assert sorted(report["status"] for report in reports) == ["queued", "running"]
    assert len(dispatched) == 1


def test_broker_failure_releases_slot_for_another_run(two_users, make_run, monkeypatch, metrics):
    scheduler = RunScheduler(max_concurrent=1)
    failed = make_run(two_users[0])
    healthy = make_run(two_users[1])
    attempts = []
    dispatched = []

    def dispatch(run_id):
        attempts.append(run_id)
        if run_id == failed.id:
            raise ConnectionError("Simulated broker dispatch failure")
        dispatched.append(run_id)

    monkeypatch.setattr(scheduler_module, "start_run", dispatch)
    raised = False
    try:
        scheduler.submit_run(failed.id)
    except ConnectionError:
        # Either propagating or handling the broker error is acceptable.
        raised = True
    after_failure = _snapshot([failed.id])
    healthy_outcome = scheduler.submit_run(healthy.id)
    observed = _record(
        metrics, [failed.id, healthy.id], dispatched,
        dispatch_attempts=attempts, broker_error_propagated=raised,
        active_count_after_failure=after_failure["active_count"],
        status_after_failure=after_failure["statuses"][failed.id],
        healthy_admission_result=healthy_outcome,
    )
    assert failed.id in attempts, "The failing broker path was not exercised"
    assert after_failure["active_count"] == 0, "Failed dispatch stranded a running slot"
    assert after_failure["statuses"][failed.id] != "running"
    assert healthy_outcome == "running"
    assert observed["statuses"][healthy.id] == "running"
    assert observed["active_count"] == 1
    assert dispatched == [healthy.id]
