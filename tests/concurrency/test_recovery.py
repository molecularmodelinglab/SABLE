"""Durable outbox recovery, application lifecycle and isolated migration parity."""
import asyncio
from datetime import datetime, timedelta, timezone
import threading

import pytest

from server.database import Base, engine, get_db_context
from server.models.run import Run
from server.models.run_dispatch import RunDispatch, SchedulerState
from server.services import run_scheduler
from server.services.run_execution import claim_run


@pytest.fixture
def reservation(two_users, make_run, monkeypatch):
    messages = []
    monkeypatch.setattr(run_scheduler, "start_run", lambda run_id, token: messages.append((run_id, token)))
    scheduler = run_scheduler.RunScheduler(max_concurrent=1)
    run = make_run(two_users[0])
    scheduler.submit_run(run.id)
    return run, scheduler, messages


def test_reservation_timeout_rotates_generation_and_rejects_old_message(reservation):
    run, scheduler, messages = reservation
    old_token = messages[0][1]
    with get_db_context() as db, db.begin():
        db.get(RunDispatch, run.id).reservation_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    scheduler.reconcile()
    assert len(messages) == 2
    assert messages[-1][1] != old_token
    assert claim_run(run.id, old_token) is None
    lease = claim_run(*messages[-1])
    assert lease is not None and lease.finish("completed")


def test_publish_crash_redelivers_same_token_but_claims_once(reservation):
    run, scheduler, messages = reservation
    token = messages[0][1]
    # Durable state just before an acknowledged publish is recorded in SQL.
    with get_db_context() as db, db.begin():
        dispatch = db.get(RunDispatch, run.id)
        dispatch.state = "reserved"
        dispatch.publication_owner = "lost-publisher"
        dispatch.publication_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    scheduler.reconcile()
    assert messages == [(run.id, token), (run.id, token)]
    lease = claim_run(run.id, token)
    assert lease is not None
    assert claim_run(run.id, token) is None
    assert lease.finish("completed")
    assert claim_run(run.id, token) is None


def test_broker_lost_ack_after_worker_claim_does_not_release_execution(two_users, make_run, monkeypatch):
    run = make_run(two_users[0])
    leases = []

    def delivered_but_no_ack(run_id, token):
        leases.append(claim_run(run_id, token))
        raise ConnectionError("Broker ACK was lost after delivery")

    monkeypatch.setattr(run_scheduler, "start_run", delivered_but_no_ack)
    scheduler = run_scheduler.RunScheduler(max_concurrent=1)
    assert scheduler.submit_run(run.id) == "queued"
    scheduler.reconcile()
    assert len(leases) == 1 and leases[0] is not None
    with get_db_context() as db:
        assert db.get(RunDispatch, run.id).state == "running"
        assert db.get(Run, run.id).status == "running"
    assert leases[0].finish("completed")


def test_legacy_running_without_lease_never_expires(two_users, make_run, monkeypatch):
    legacy = make_run(two_users[0], status="running")
    waiting = make_run(two_users[1])
    messages = []
    monkeypatch.setattr(run_scheduler, "start_run", lambda *args: messages.append(args))
    with get_db_context() as db, db.begin():
        db.get(Run, legacy.id).updated_at = datetime.now(timezone.utc) - timedelta(days=30)
    scheduler = run_scheduler.RunScheduler(max_concurrent=1)
    scheduler.submit_run(waiting.id)
    scheduler.reconcile()
    with get_db_context() as db:
        assert db.get(Run, legacy.id).status == "running"
        assert db.get(RunDispatch, legacy.id) is None
        assert db.get(RunDispatch, waiting.id).state == "pending"
    assert messages == []


def test_periodic_reconciler_recovers_after_error_and_stops(monkeypatch):
    from server.services import scheduler_lifecycle

    recovered = threading.Event()
    calls = []

    def reconcile():
        calls.append(threading.current_thread().name)
        if len(calls) == 1:
            raise ConnectionError("Transient recovery failure")
        recovered.set()

    monkeypatch.setattr(scheduler_lifecycle.run_scheduler, "reconcile", reconcile)
    monkeypatch.setattr(scheduler_lifecycle, "RECONCILE_INTERVAL_SECONDS", 0.02)

    async def exercise():
        async with scheduler_lifecycle.scheduler_lifespan(None):
            assert await asyncio.to_thread(recovered.wait, 2)
        count = len(calls)
        await asyncio.sleep(0.08)
        assert len(calls) == count

    asyncio.run(exercise())
    assert len(calls) >= 2
    assert set(calls) == {"run-reconciler"}
    assert not any(t.name == "run-reconciler" for t in threading.enumerate())


def test_slow_reconcile_does_not_block_event_loop_and_shutdown_is_bounded(monkeypatch):
    from server.services import scheduler_lifecycle

    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def slow():
        entered.set()
        try:
            assert release.wait(4)
        finally:
            exited.set()

    monkeypatch.setattr(scheduler_lifecycle.run_scheduler, "reconcile", slow)
    monkeypatch.setattr(scheduler_lifecycle, "SHUTDOWN_TIMEOUT_SECONDS", 0.05)

    async def exercise():
        async with scheduler_lifecycle.scheduler_lifespan(None):
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.wait_for(asyncio.sleep(0), timeout=0.2)
        assert not release.is_set()

    try:
        asyncio.run(asyncio.wait_for(exercise(), timeout=2))
    finally:
        release.set()
        assert exited.wait(2)
        for thread in threading.enumerate():
            if thread.name == "run-reconciler":
                thread.join(timeout=2)


def test_periodic_recovery_retries_broker_without_another_submission(two_users, make_run, monkeypatch):
    from server.services import scheduler_lifecycle

    run = make_run(two_users[0])
    messages = []

    def broker_unavailable(run_id, token):
        messages.append((run_id, token))
        raise ConnectionError("Transient broker failure")

    monkeypatch.setattr(run_scheduler, "start_run", broker_unavailable)
    run_scheduler.RunScheduler(max_concurrent=1).submit_run(run.id)
    with get_db_context() as db, db.begin():
        dispatch = db.get(RunDispatch, run.id)
        assert dispatch.state == "pending"
        dispatch.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    published = threading.Event()

    def publish(run_id, token):
        messages.append((run_id, token))
        published.set()

    monkeypatch.setattr(run_scheduler, "start_run", publish)
    monkeypatch.setattr(scheduler_lifecycle, "RECONCILE_INTERVAL_SECONDS", 0.02)

    async def exercise():
        async with scheduler_lifecycle.scheduler_lifespan(None):
            assert await asyncio.to_thread(published.wait, 2)

    asyncio.run(exercise())
    assert len(messages) == 2
    assert messages[0][1] != messages[1][1]


def test_dispatch_migration_upgrade_downgrade_matches_models():
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect

    revision = ScriptDirectory.from_config(Config("/suite/alembic.ini")).get_revision("9c5d2e71b804")
    assert revision.down_revision == "4f3b10a792ce"
    names = {RunDispatch.__tablename__, SchedulerState.__tablename__}
    # All DDL is rolled back, including the baseline-table drops, on this disposable DB.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            RunDispatch.__table__.drop(connection)
            SchedulerState.__table__.drop(connection)
            context = MigrationContext.configure(connection, opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_object": lambda obj, name, type_, reflected, compare_to: (
                    name in names if type_ == "table" else True
                ),
            })
            with Operations.context(context):
                revision.module.upgrade()
            assert compare_metadata(context, Base.metadata) == []
            inspector = inspect(connection)
            assert {item["name"] for item in inspector.get_check_constraints("run_scheduler_state")} == {
                "ck_run_scheduler_singleton", "ck_run_scheduler_positive_limit",
            }
            assert {item["name"] for item in inspector.get_check_constraints("run_dispatches")} == {"ck_run_dispatch_state"}
            assert inspector.get_pk_constraint("run_dispatches")["constrained_columns"] == ["run_id"]
            foreign_key, = inspector.get_foreign_keys("run_dispatches")
            assert foreign_key["referred_table"] == "runs"
            assert foreign_key["options"]["ondelete"] == "CASCADE"
            with Operations.context(context):
                revision.module.downgrade()
            assert names.isdisjoint(inspect(connection).get_table_names())
            assert "runs" in inspect(connection).get_table_names()
        finally:
            transaction.rollback()


@pytest.mark.parametrize("terminal", ["completed", "failed", "stopped"])
@pytest.mark.parametrize("stale", ["queued", "running"])
def test_late_active_cache_fill_cannot_overwrite_terminal_state(reservation, terminal, stale):
    from server.services.cache_service import cache_service

    run, scheduler, messages = reservation
    lease = claim_run(*messages[0])
    if terminal == "failed":
        with get_db_context() as db, db.begin():
            db.get(RunDispatch, run.id).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        scheduler.reconcile()
    else:
        assert lease.finish(terminal)
    user_id = str(run.user_id)
    final = {"id": run.id, "status": terminal}
    old = {"id": run.id, "status": stale}
    assert cache_service.cache_run(run.id, final)
    assert cache_service.cache_run_status(run.id, final)
    assert cache_service.cache_user_runs_list(user_id, [final])
    assert not cache_service.cache_run(run.id, old)
    assert not cache_service.cache_run_status(run.id, old)
    assert not cache_service.cache_user_runs_list(user_id, [final, old])
    assert cache_service.get_cached_run(run.id) == final
    assert cache_service.get_cached_run_status(run.id) == final
    assert cache_service.get_cached_user_runs_list(user_id) == [final]
    with get_db_context() as db:
        assert db.get(Run, run.id).status == terminal


@pytest.mark.parametrize("legacy", ["queued", "running", "unknown"])
def test_legacy_active_cache_snapshots_are_ignored(reservation, legacy):
    import json
    from server.services.cache_service import cache_service

    run, _, _ = reservation
    data = {"id": run.id, "status": legacy}
    user_id = str(run.user_id)
    cache_service.redis.set(f"run:{run.id}", json.dumps(data))
    cache_service.redis.set(f"run:status:{run.id}", json.dumps(data))
    cache_service.redis.set(f"user:{user_id}:runs", json.dumps([data]))
    assert cache_service.get_cached_run(run.id) is None
    assert cache_service.get_cached_run_status(run.id) is None
    assert cache_service.get_cached_user_runs_list(user_id) is None


def test_api_wires_scheduler_lifespan_without_importing_providers():
    import ast
    from pathlib import Path

    module = ast.parse(Path("/suite/server/app.py").read_text())
    imports = [node for node in ast.walk(module) if isinstance(node, ast.ImportFrom)
               and node.module == "server.services.scheduler_lifecycle"]
    assert any(alias.name == "scheduler_lifespan" for node in imports for alias in node.names)
    apps = [node for node in ast.walk(module) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "FastAPI"]
    assert any(keyword.arg == "lifespan" and isinstance(keyword.value, ast.Name)
               and keyword.value.id == "scheduler_lifespan"
               for app in apps for keyword in app.keywords)
