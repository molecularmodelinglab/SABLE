"""Real Celery wrapper and SQL ownership tests without molecular execution."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import time

import pytest

from server.database import get_db_context
from server.models.run import Run
from server.models.run_dispatch import RunDispatch
from server.services import run_execution, run_scheduler
from server.services.run_execution import LeaseLost, claim_run


@pytest.fixture
def queued_run(two_users, make_run, monkeypatch):
    messages = []
    monkeypatch.setattr(run_scheduler, "start_run", lambda run_id, token: messages.append((run_id, token)))
    scheduler = run_scheduler.RunScheduler(max_concurrent=1)
    run = make_run(two_users[0])
    assert scheduler.submit_run(run.id) == "queued"
    assert len(messages) == 1
    return run, messages[0][1], scheduler, messages


def expire(run_id, column):
    with get_db_context() as db, db.begin():
        setattr(db.get(RunDispatch, run_id), column, datetime.now(timezone.utc) - timedelta(seconds=1))


def status(run_id):
    with get_db_context() as db:
        return db.get(Run, run_id).status


def test_only_one_competing_worker_claims_same_generation(queued_run):
    run, token, _, _ = queued_run
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait(timeout=3)
        return claim_run(run.id, token)

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        leases = [future.result(timeout=5) for future in futures]
    assert sum(lease is not None for lease in leases) == 1
    lease = next(lease for lease in leases if lease is not None)
    assert status(run.id) == "running"
    assert lease.finish("completed")
    assert claim_run(run.id, token) is None


@pytest.mark.parametrize("delivery", ["missing", "wrong", "expired"])
def test_task_rejects_missing_stale_or_unclaimed_token(queued_run, monkeypatch, delivery):
    from server.tasks import workflow

    run, token, _, _ = queued_run
    calls = []
    monkeypatch.setattr(workflow, "_execute_workflow", lambda *args: calls.append(args))
    if delivery == "missing":
        workflow.run_workflow_task.run(run.id)
    else:
        if delivery == "expired":
            expire(run.id, "reservation_deadline")
        workflow.run_workflow_task.run(run.id, "not-current" if delivery == "wrong" else token)
    assert calls == []
    assert status(run.id) == "queued"


def test_actual_task_redelivery_executes_once(queued_run, monkeypatch):
    from server.tasks import workflow

    run, token, _, _ = queued_run
    entered, release = threading.Event(), threading.Event()
    calls = []

    def execute(run_id, lease):
        calls.append((run_id, lease.owner))
        assert status(run_id) == "running"
        entered.set()
        assert release.wait(5)
        return "completed", None, ["C"]

    monkeypatch.setattr(workflow, "_execute_workflow", execute)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(workflow.run_workflow_task.run, run.id, token)
        try:
            assert entered.wait(3)
            pool.submit(workflow.run_workflow_task.run, run.id, token).result(timeout=2)
        finally:
            release.set()
        first.result(timeout=3)
    workflow.run_workflow_task.run(run.id, token)
    assert len(calls) == 1
    assert status(run.id) == "completed"
    with get_db_context() as db:
        assert db.get(Run, run.id).starting_molecules == ["C"]


def test_actual_task_preparation_failure_finishes_and_releases_slot(queued_run, two_users, make_run, monkeypatch):
    from server.tasks import workflow

    run, token, scheduler, messages = queued_run
    waiting = make_run(two_users[1])
    scheduler.submit_run(waiting.id)

    def failed_preparation(run_id, lease):
        raise OSError("controlled preparation failure")

    monkeypatch.setattr(workflow, "_execute_workflow", failed_preparation)
    with pytest.raises(OSError, match="controlled preparation failure"):
        workflow.run_workflow_task.run(run.id, token)
    assert status(run.id) == "failed"
    assert [item[0] for item in messages] == [run.id, waiting.id]
    with get_db_context() as db:
        assert "controlled preparation failure" in db.get(Run, run.id).exit_reason
    log_path = workflow.run_dir(run.id) / "logs" / "logs.ndjson"
    assert "controlled preparation failure" in log_path.read_text()


def test_expired_worker_is_failed_never_restarted_and_cannot_write(queued_run, two_users, make_run):
    run, token, scheduler, messages = queued_run
    lease = claim_run(run.id, token)
    assert lease is not None
    waiting = make_run(two_users[1])
    scheduler.submit_run(waiting.id)
    expire(run.id, "lease_expires_at")
    scheduler.reconcile()
    assert status(run.id) == "failed"
    assert [item[0] for item in messages] == [run.id, waiting.id]
    assert not lease.update_molecules(["stale"])
    assert not lease.finish("completed")
    assert not lease.renew()
    with pytest.raises(LeaseLost):
        lease.check()
    assert claim_run(run.id, token) is None
    assert scheduler.submit_run(run.id) == "failed"
    scheduler.reconcile()
    assert [item[0] for item in messages].count(run.id) == 1
    with get_db_context() as db:
        model = db.get(Run, run.id)
        assert model.starting_molecules == []
        assert "lease expired" in model.exit_reason.lower()


@pytest.mark.parametrize("operation", ["finish", "update_molecules", "renew"])
def test_expired_owner_is_fenced_before_reconciliation(queued_run, operation):
    run, token, _, _ = queued_run
    lease = claim_run(run.id, token)
    expire(run.id, "lease_expires_at")
    args = {"finish": ("completed",), "update_molecules": (["stale"],), "renew": ()}[operation]
    assert not getattr(lease, operation)(*args)
    with pytest.raises(LeaseLost):
        lease.check()
    with get_db_context() as db:
        assert db.get(Run, run.id).status == "running"
        assert db.get(Run, run.id).starting_molecules == []


def test_quiet_workflow_renews_lease_independently_of_progress(queued_run, monkeypatch):
    from server.tasks import workflow

    run, token, scheduler, _ = queued_run
    monkeypatch.setattr(run_execution, "HEARTBEAT_SECONDS", 0.03)
    monkeypatch.setattr(run_execution, "EXECUTION_LEASE_SECONDS", 0.5)

    def quiet_execution(run_id, lease):
        with get_db_context() as db:
            initial_heartbeat = db.get(RunDispatch, run_id).heartbeat_at
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            scheduler.reconcile()
            time.sleep(0.04)
        with get_db_context() as db:
            dispatch = db.get(RunDispatch, run_id)
            assert dispatch.heartbeat_at > initial_heartbeat
            assert dispatch.state == "running"
        lease.check()
        return "completed", None, []

    monkeypatch.setattr(workflow, "_execute_workflow", quiet_execution)
    workflow.run_workflow_task.run(run.id, token)
    assert status(run.id) == "completed"
    assert not any(thread.name == f"run-heartbeat-{run.id}" for thread in threading.enumerate())


def test_renewal_database_error_loses_authority(queued_run, monkeypatch):
    run, token, _, _ = queued_run
    lease = claim_run(run.id, token)

    def unavailable():
        raise ConnectionError("controlled database failure")

    monkeypatch.setattr(run_execution, "get_db_context", unavailable)
    assert not lease.renew()
    with pytest.raises(LeaseLost):
        lease.check()
    assert not lease.finish("completed")
    assert not lease.update_molecules(["stale"])


def test_lease_loss_in_actual_task_does_not_finalize(queued_run, monkeypatch):
    from server.tasks import workflow

    run, token, scheduler, _ = queued_run

    def lose_ownership(run_id, lease):
        expire(run_id, "lease_expires_at")
        scheduler.reconcile()
        assert not lease.renew()
        lease.check()

    monkeypatch.setattr(workflow, "_execute_workflow", lose_ownership)
    workflow.run_workflow_task.run(run.id, token)
    assert status(run.id) == "failed"


def test_launcher_forwards_generation_and_disables_task_publish_retry(monkeypatch):
    from unittest.mock import MagicMock
    from server.services.run_launcher import start_run
    from server.tasks.celery_app import celery_app
    from server.tasks.workflow import run_workflow_task

    connection = MagicMock()
    connection_for_write = MagicMock()
    connection_for_write.return_value.__enter__.return_value = connection
    apply_async = MagicMock()
    monkeypatch.setattr(celery_app, "connection_for_write", connection_for_write)
    monkeypatch.setattr(run_workflow_task, "apply_async", apply_async)
    start_run("controlled-run", "generation-token")
    connection_for_write.assert_called_once_with(connect_timeout=5)
    apply_async.assert_called_once_with(args=("controlled-run", "generation-token"),
                                       task_id="generation-token", connection=connection,
                                       retry=False, ignore_result=True)
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.broker_transport_options["socket_connect_timeout"] == 5
    assert celery_app.conf.broker_transport_options["socket_timeout"] == 5
    assert celery_app.conf.broker_transport_options["max_retries"] == 0


def test_actual_launcher_publishes_token_to_isolated_redis(queued_run):
    import base64
    import json
    import os
    import redis
    from server.services.run_launcher import start_run
    from server.tasks.celery_app import celery_app

    run, token, _, _ = queued_run
    broker_url = os.environ["CELERY_BROKER_URL"]
    assert broker_url == "redis://redis:6379/1"
    assert celery_app.conf.broker_url == broker_url
    broker = redis.Redis.from_url(broker_url)
    try:
        broker.flushdb()
        start_run(run.id, token)
        raw = broker.lpop("celery")
        assert raw is not None
        message = json.loads(raw)
        args, kwargs, _ = json.loads(base64.b64decode(message["body"]))
        assert args == [run.id, token]
        assert kwargs == {}
        assert message["headers"]["id"] == token
        assert message["headers"]["task"] == "server.tasks.workflow.run_workflow"
        assert message["headers"]["ignore_result"] is True
        assert broker.llen("celery") == 0
        assert status(run.id) == "queued"
    finally:
        broker.flushdb()
        broker.close()


def test_worker_stopped_status_is_terminal(queued_run):
    run, token, scheduler, messages = queued_run
    lease = claim_run(run.id, token)
    assert lease.finish("stopped")
    scheduler.reconcile()
    assert scheduler.submit_run(run.id) == "stopped"
    assert len(messages) == 1
    with get_db_context() as db:
        assert db.get(RunDispatch, run.id).state == "finished"


def test_heartbeat_thread_start_failure_fails_run_without_execution(queued_run, monkeypatch):
    from server.tasks import workflow

    run, token, _, _ = queued_run
    calls = []

    def cannot_start(thread):
        raise RuntimeError("controlled thread start failure")

    monkeypatch.setattr(threading.Thread, "start", cannot_start)
    monkeypatch.setattr(workflow, "_execute_workflow", lambda *args: calls.append(args))
    with pytest.raises(RuntimeError, match="controlled thread start failure"):
        workflow.run_workflow_task.run(run.id, token)
    assert calls == []
    assert status(run.id) == "failed"
    with get_db_context() as db:
        assert db.get(RunDispatch, run.id).state == "failed"


@pytest.mark.parametrize("operation", ["claim", "finish"])
def test_lock_wait_uses_fresh_database_expiry(queued_run, operation):
    run, token, _, _ = queued_run
    lease = claim_run(run.id, token) if operation == "finish" else None
    entered = threading.Event()

    def attempt():
        entered.set()
        return claim_run(run.id, token) if operation == "claim" else lease.finish("completed")

    with ThreadPoolExecutor(1) as pool:
        with get_db_context() as db, db.begin():
            dispatch = db.query(RunDispatch).filter_by(run_id=run.id).with_for_update().one()
            pending = pool.submit(attempt)
            assert entered.wait(2)
            from sqlalchemy import text
            deadline = time.monotonic() + 2
            blocked = False
            while time.monotonic() < deadline:
                with get_db_context() as observer:
                    blocked = observer.execute(text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                        "AND wait_event_type = 'Lock' AND query LIKE '%run_dispatches%'"
                    )).scalar() > 0
                if blocked:
                    break
                time.sleep(0.01)
            assert blocked, "Worker must actually wait on the dispatch lock before expiry changes"
            column = "reservation_deadline" if operation == "claim" else "lease_expires_at"
            # Expire after the waiter's transaction began, before releasing its lock.
            setattr(dispatch, column, datetime.now(timezone.utc))
        result = pending.result(timeout=3)
    assert (result is None) if operation == "claim" else (result is False)
    assert status(run.id) == ("queued" if operation == "claim" else "running")


def test_actual_helper_propagates_lease_loss_from_workflow_callback(queued_run, monkeypatch):
    from types import SimpleNamespace
    from server.tasks import workflow

    run, token, scheduler, _ = queued_run
    lease = claim_run(run.id, token)
    logs, exports, syncs = [], [], []

    def execute(**kwargs):
        expire(run.id, "lease_expires_at")
        scheduler.reconcile()
        assert not lease.renew()
        kwargs["event_callback"]({"message": "stale event", "starting_molecules": ["C"]})
        raise AssertionError("Lost callback must stop execution")

    runner = SimpleNamespace(checkpoint_dir=workflow.run_dir(run.id) / "checkpoints", run=execute,
                             export_results=lambda *args: exports.append(args))
    monkeypatch.setattr(workflow, "WorkflowRunner", lambda **kwargs: runner)
    monkeypatch.setattr(workflow.run_event_hub, "append_log", lambda *args: logs.append(args))
    monkeypatch.setattr(workflow, "sync_run", lambda *args: syncs.append(args))
    with pytest.raises(LeaseLost):
        workflow._execute_workflow(run.id, lease)
    assert logs == exports == syncs == []
    assert status(run.id) == "failed"


def test_heartbeat_start_failure_cannot_overwrite_expired_owner(queued_run, monkeypatch):
    run, token, scheduler, _ = queued_run
    lease = claim_run(run.id, token)
    expire(run.id, "lease_expires_at")
    scheduler.reconcile()
    with get_db_context() as db:
        reason = db.get(Run, run.id).exit_reason

    def cannot_start(thread):
        raise RuntimeError("controlled thread start failure")

    monkeypatch.setattr(threading.Thread, "start", cannot_start)
    with pytest.raises(RuntimeError, match="controlled thread start failure"):
        with lease:
            raise AssertionError("Expired owner must not execute")
    with get_db_context() as db:
        assert db.get(Run, run.id).status == "failed"
        assert db.get(Run, run.id).exit_reason == reason


def test_delayed_renewal_cannot_revive_local_expired_lease(queued_run, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    run, token, _, _ = queued_run
    lease = claim_run(run.id, token)
    clock = [time.monotonic()]
    original_context = run_execution.get_db_context

    @contextmanager
    def delayed_commit():
        with original_context() as db:
            yield db
        clock[0] += run_execution.EXECUTION_LEASE_SECONDS + 1

    monkeypatch.setattr(run_execution, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(run_execution, "get_db_context", delayed_commit)
    assert not lease.renew()
    with pytest.raises(LeaseLost):
        lease.check()
    assert not lease.finish("completed")


def test_redis_invalidation_errors_do_not_undo_worker_claim_or_finish(queued_run, monkeypatch):
    from server.services.cache_service import cache_service

    run, token, _, _ = queued_run

    def unavailable(*args, **kwargs):
        raise ConnectionError("controlled Redis failure")

    monkeypatch.setattr(cache_service, "invalidate_run", unavailable)
    lease = claim_run(run.id, token)
    assert lease is not None
    assert status(run.id) == "running"
    assert lease.finish("completed")
    assert status(run.id) == "completed"
