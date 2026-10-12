"""Fair admission and authorized queue explanations against isolated PostgreSQL/Redis."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event

from server.database import engine, get_db_context
from server.models.run import Run
from server.models.run_dispatch import RunDispatch
from server.models.user import User
from server.services import run_scheduler
from server.services.run_execution import claim_run
from test_scheduler import _process_schedulers


pytestmark = pytest.mark.integration


def _seed(make_run, user, count=1, *, state="pending", age=0, same_time=False):
    runs = [make_run(user, status="running" if state in {"running", "legacy"} else "queued")
            for _ in range(count)]
    with get_db_context() as db, db.begin():
        now = run_scheduler.database_now(db)
        for index, run in enumerate(runs):
            db.get(Run, run.id).created_at = now - timedelta(days=1) + timedelta(
                seconds=age + (0 if same_time else index))
            if state != "legacy":
                db.add(RunDispatch(
                    run_id=run.id, state=state, next_attempt_at=now - timedelta(seconds=1),
                    dispatch_token=str(uuid4()) if state != "pending" else None,
                    reservation_deadline=now + timedelta(hours=1) if state != "pending" else None,
                    publication_owner=str(uuid4()) if state == "reserved" else None,
                    publication_lease_expires_at=now + timedelta(hours=1) if state == "reserved" else None,
                    execution_owner=str(uuid4()) if state == "running" else None,
                    lease_expires_at=now + timedelta(hours=1) if state == "running" else None,
                ))
    return sorted(runs, key=lambda run: run.id) if same_time else runs


def _states(runs):
    with get_db_context() as db:
        return {row.run_id: row.state for row in db.query(RunDispatch).filter(
            RunDispatch.run_id.in_([run.id for run in runs]))}


def _admitted(runs):
    return {run_id for run_id, state in _states(runs).items()
            if state in run_scheduler.SLOT_STATES}


@pytest.fixture
def scheduler(monkeypatch):
    monkeypatch.setattr(run_scheduler, "start_run", lambda run_id, token: None)
    return run_scheduler.RunScheduler(max_concurrent=5)


@pytest.mark.parametrize("admin_index", [None, 0, 1])
def test_fairness_recounts_each_reservation_and_admin_has_no_priority(
    two_users, make_run, scheduler, admin_index, metrics,
):
    if admin_index is not None:
        with get_db_context() as db, db.begin():
            user = db.get(User, two_users[admin_index].id)
            user.extra_metadata = {**user.extra_metadata, "roles": ["admin"]}
    alice = _seed(make_run, two_users[0], 6, age=0)
    bob = _seed(make_run, two_users[1], 6, age=100)
    scheduler.reconcile()
    selected = _admitted(alice + bob)
    counts = Counter(str(run.user_id) for run in alice + bob if run.id in selected)
    metrics(admitted_by_user=dict(counts), admitted_total=len(selected), admin_index=admin_index)
    assert selected == {run.id for run in alice[:3] + bob[:2]}
    assert len(selected) == 5
    scheduler.reconcile()
    assert _admitted(alice + bob) == selected


def test_fairness_one_user_can_use_all_five_slots(two_users, make_run, scheduler):
    waiting = _seed(make_run, two_users[0], 8)
    scheduler.reconcile()
    assert _admitted(waiting) == {run.id for run in waiting[:5]}


def test_fairness_saturated_user_yields_freed_slots_without_preemption(
    two_users, make_run, scheduler,
):
    existing = _seed(make_run, two_users[0], 5, state="running")
    alice = _seed(make_run, two_users[0], 3)
    bob = _seed(make_run, two_users[1], 3, age=100)
    with get_db_context() as db:
        generations = {run.id: db.get(RunDispatch, run.id).dispatch_token for run in existing}
    scheduler.reconcile()
    assert _admitted(alice + bob) == set()
    with get_db_context() as db, db.begin():
        for run in existing[:2]:
            db.get(Run, run.id).status = "completed"
    scheduler.reconcile()
    assert _admitted(alice + bob) == {run.id for run in bob[:2]}
    with get_db_context() as db:
        for run in existing[2:]:
            assert db.get(Run, run.id).status == "running"
            assert db.get(RunDispatch, run.id).dispatch_token == generations[run.id]


@pytest.mark.parametrize("state", ["reserved", "dispatched", "running", "legacy"])
def test_fairness_counts_each_admitted_state_by_user(
    two_users, make_run, scheduler, state,
):
    existing = _seed(make_run, two_users[0], 3, state=state)
    alice = _seed(make_run, two_users[0], 3)
    bob = _seed(make_run, two_users[1], 3, age=100)
    scheduler.reconcile()
    assert _admitted(alice + bob) == {run.id for run in bob[:2]}
    with get_db_context() as db:
        for run in existing:
            if state == "legacy":
                assert db.get(RunDispatch, run.id) is None
                assert db.get(Run, run.id).status == "running"
            else:
                assert db.get(RunDispatch, run.id).state == state


@pytest.mark.parametrize("owners,selected_indices", [
    pytest.param([0, 0, 0, 0, 1, 1, 1, 1], [0, 1, 2, 4, 5], id="first-user-wins-fifth-slot"),
    pytest.param([0, 1, 1, 1, 0, 0, 0, 1], [0, 1, 2, 3, 4], id="other-user-wins-fifth-slot"),
])
def test_fairness_oldest_prompt_then_run_id_breaks_equal_counts(
    two_users, make_run, scheduler, owners, selected_indices,
):
    alice = _seed(make_run, two_users[0], 4, same_time=True)
    bob = _seed(make_run, two_users[1], 4, same_time=True)
    all_runs = sorted(alice + bob, key=lambda run: run.id)
    with get_db_context() as db, db.begin():
        timestamp = run_scheduler.database_now(db) - timedelta(days=1)
        for run, owner in zip(all_runs, owners):
            stored = db.get(Run, run.id)
            stored.created_at = timestamp
            stored.user_id = two_users[owner].id
    # The fifth slot compares the users' next prompts, not their first prompts.
    scheduler.reconcile()
    assert _admitted(all_runs) == {all_runs[index].id for index in selected_indices}


def test_fairness_backoff_skips_oldest_then_rejoins_naturally(
    two_users, make_run, scheduler,
):
    _seed(make_run, two_users[0], 3, state="running")
    alice = _seed(make_run, two_users[0], 2)
    bob = _seed(make_run, two_users[1], 4, age=100)
    with get_db_context() as db, db.begin():
        db.get(RunDispatch, bob[0].id).next_attempt_at = (
            run_scheduler.database_now(db) + timedelta(hours=1))
    scheduler.reconcile()
    assert _admitted(alice + bob) == {bob[1].id, bob[2].id}
    with get_db_context() as db, db.begin():
        db.get(Run, bob[1].id).status = "completed"
        db.get(RunDispatch, bob[0].id).next_attempt_at = (
            run_scheduler.database_now(db) - timedelta(seconds=1))
    scheduler.reconcile()
    assert _admitted(alice + bob) == {bob[0].id, bob[2].id}


@pytest.mark.parametrize("concurrency", ["instances", "processes"])
def test_fairness_competing_schedulers_serialize_counts_and_shared_five(
    two_users, make_run, scheduler, concurrency, metrics,
):
    alice = _seed(make_run, two_users[0], 6)
    bob = _seed(make_run, two_users[1], 6, age=100)
    if concurrency == "processes":
        reports = _process_schedulers("drain", [alice[0].id, bob[0].id])
        assert len(reports) == 2 and not any("error" in report for report in reports), reports
        publications = [run_id for report in reports for run_id in report["dispatches"]]
        assert len(publications) == len(set(publications)) == 5
        metrics(worker_reports=reports)
    else:
        barrier = threading.Barrier(2)

        def reconcile():
            barrier.wait(timeout=3)
            run_scheduler.RunScheduler().reconcile()

        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(reconcile) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
    assert _admitted(alice + bob) == {run.id for run in alice[:3] + bob[:2]}


@pytest.fixture
def queue_headers(api, two_users, make_run):
    from server.auth.jwt import create_access_token
    from server.routers import admin

    api.include_router(admin.router)
    api.dependency_overrides.clear()
    return {str(user.id): {"Authorization": "Bearer " + create_access_token({
        "user_id": str(user.id), "session_id": str(make_run(user).session_id),
    })} for user in two_users}


def _assert_queue(data, reason, retry_at=None):
    assert "queue_reason" in data and data["queue_reason"] == reason
    assert "queue_retry_at" in data
    if retry_at is None:
        assert data["queue_retry_at"] is None
    else:
        assert datetime.fromisoformat(data["queue_retry_at"].replace("Z", "+00:00")) == retry_at
    for name in ("dispatch_token", "execution_owner", "publication_owner", "queue_position"):
        assert name not in data


def test_queue_http_detail_list_all_reasons_legacy_and_cross_user_ownership(
    live_api, queue_headers, two_users, make_run,
):
    alice, bob = two_users
    expected = {}
    legacy = make_run(alice, status="queued")
    expected[legacy.id] = ("waiting_for_admission", None)
    for state, reason in (("pending", "waiting_for_admission"), ("reserved", "dispatching"),
                          ("dispatched", "waiting_for_worker"), ("running", None)):
        run = _seed(make_run, alice, state=state)[0]
        expected[run.id] = (reason, None)
    retry = _seed(make_run, alice)[0]
    with get_db_context() as db, db.begin():
        retry_at = run_scheduler.database_now(db) + timedelta(hours=1)
        db.get(RunDispatch, retry.id).next_attempt_at = retry_at
    expected[retry.id] = ("dispatch_retry", retry_at)
    for status in ("pending", "running", "completed", "failed", "cancelled", "stopped"):
        run = make_run(alice, status=status)
        expected[run.id] = (None, None)
    stranger = _seed(make_run, bob, state="reserved")[0]
    with httpx.Client(base_url=live_api, timeout=8) as client:
        headers = queue_headers[str(alice.id)]
        response = client.get("/runs", headers=headers)
        assert response.status_code == 200, response.text
        listing = {row["id"]: row for row in response.json()["runs"]}
        assert stranger.id not in listing
        assert str(bob.id) not in response.text
        for run_id, (reason, retry_at) in expected.items():
            _assert_queue(listing[run_id], reason, retry_at)
            detail = client.get(f"/runs/{run_id}", headers=headers)
            assert detail.status_code == 200, detail.text
            _assert_queue(detail.json(), reason, retry_at)
            forbidden = client.get(f"/runs/{run_id}", headers=queue_headers[str(bob.id)])
            assert forbidden.status_code in (403, 404)
            assert "queue_reason" not in forbidden.text
            assert str(alice.id) not in forbidden.text


@pytest.mark.parametrize("at_capacity", [False, True])
def test_queue_http_create_reports_dispatch_or_admission(
    live_api, queue_headers, two_users, make_run, scheduler, at_capacity,
):
    if at_capacity:
        _seed(make_run, two_users[1], 5, state="legacy")
    with httpx.Client(base_url=live_api, timeout=8) as client:
        response = client.post("/runs", headers=queue_headers[str(two_users[0].id)],
                               json={"prompt": "explain this queued single prompt"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "queued"
        _assert_queue(response.json(), "waiting_for_admission" if at_capacity else "waiting_for_worker")


def test_queue_http_admin_routes_and_non_admin_rejection(
    live_api, queue_headers, two_users, make_run,
):
    alice, bob = two_users
    with get_db_context() as db, db.begin():
        user = db.get(User, alice.id)
        user.extra_metadata = {**user.extra_metadata, "roles": ["admin"]}
    run = _seed(make_run, bob, state="dispatched")[0]
    with httpx.Client(base_url=live_api, timeout=8) as client:
        for path in ("/admin/runs", f"/admin/runs/{run.id}"):
            rejected = client.get(path, headers=queue_headers[str(bob.id)])
            assert rejected.status_code == 403, rejected.text
            assert "queue_reason" not in rejected.text
            response = client.get(path, headers=queue_headers[str(alice.id)])
            assert response.status_code == 200, response.text
            data = response.json()
            if path == "/admin/runs":
                data = next(row for row in data["runs"] if row["id"] == run.id)
            _assert_queue(data, "waiting_for_worker")


def test_queue_http_polling_retry_dispatch_running_clears_uncached_metadata(
    live_api, queue_headers, two_users, make_run, scheduler, monkeypatch,
):
    run = make_run(two_users[0])

    def broker_down(*args):
        raise ConnectionError("isolated broker rejection")

    monkeypatch.setattr(run_scheduler, "start_run", broker_down)
    scheduler.submit_run(run.id)
    with get_db_context() as db, db.begin():
        retry_at = run_scheduler.database_now(db) + timedelta(hours=1)
        db.get(RunDispatch, run.id).next_attempt_at = retry_at
    with httpx.Client(base_url=live_api, timeout=8) as client:
        headers = queue_headers[str(two_users[0].id)]

        def poll(reason, retry=None):
            detail = client.get(f"/runs/{run.id}", headers=headers)
            assert detail.status_code == 200, detail.text
            _assert_queue(detail.json(), reason, retry)
            listing = client.get("/runs", headers=headers)
            assert listing.status_code == 200, listing.text
            _assert_queue(next(row for row in listing.json()["runs"] if row["id"] == run.id), reason, retry)

        poll("dispatch_retry", retry_at)
        with get_db_context() as db, db.begin():
            db.get(RunDispatch, run.id).next_attempt_at = (
                run_scheduler.database_now(db) - timedelta(seconds=1))
        poll("waiting_for_admission")
        messages = []
        monkeypatch.setattr(run_scheduler, "start_run", lambda *args: messages.append(args))
        scheduler.reconcile()
        poll("waiting_for_worker")
        lease = claim_run(*messages[0])
        assert lease is not None
        poll(None)
        assert lease.finish("completed")
        poll(None)


def test_queue_enrichment_is_batched_read_only_and_scoped_to_authorized_ids(
    two_users, make_run,
):
    from server.schemas.run import RunInfo
    from server.services.run_queue import add_queue_details

    allowed = _seed(make_run, two_users[0], 8)
    unauthorized = _seed(make_run, two_users[1], 3, state="reserved")
    infos = [RunInfo(id=run.id, status="queued", created_at=run.created_at,
                     updated_at=run.updated_at, user_id=str(run.user_id)) for run in allowed]
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with get_db_context() as db:
            add_queue_details(db, infos)
            assert not db.new and not db.dirty and not db.deleted
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements and len(statements) <= 2, statements
    metadata_queries = [(sql, params) for sql, params in statements
                        if "run_dispatches" in sql.lower()]
    assert len(metadata_queries) == 1, statements
    sql, parameters = metadata_queries[0]
    assert "where" in sql.lower() and "run_id" in sql.lower().split("where", 1)[1]
    assert all(run.id in str(parameters) for run in allowed)
    assert all(run.id not in str(statements) for run in unauthorized)
    assert all(sql.lstrip().lower().startswith("select") and "for update" not in sql.lower()
               and "for share" not in sql.lower() for sql, _ in statements)
    for info in infos:
        _assert_queue(info.model_dump(), "waiting_for_admission")
