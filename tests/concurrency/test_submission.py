"""Single-prompt requests using real authentication, persistence, and scheduling."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time

import httpx
import pytest


@pytest.fixture
def authenticated_headers(api, two_users, make_run):
    from server.auth.jwt import create_access_token

    api.dependency_overrides.clear()
    return {
        str(user.id): {"Authorization": "Bearer " + create_access_token({
            "user_id": str(user.id),
            "session_id": str(make_run(user).session_id),
        })}
        for user in two_users
    }


@pytest.mark.parametrize("probe", ["run-details", "run-submission"])
@pytest.mark.parametrize("slow_stage", ["storage", "database", "cache", "broker", "authentication"])
def test_slow_submission_does_not_block_other_user(
    live_api, authenticated_headers, two_users, make_run, monkeypatch, metrics,
    probe, slow_stage,
):
    from server.routers import runs
    from server.services import run_scheduler

    alice, bob = two_users
    run = make_run(bob)
    entered, release = threading.Event(), threading.Event()
    gate = threading.Lock()
    monkeypatch.setattr(run_scheduler, "start_run", lambda run_id, token: None)
    target, name = {
        "storage": (runs, "sync_run"),
        "database": (runs.run_service, "create_run"),
        "cache": (runs.cache_service, "invalidate_user_runs_list"),
        "broker": (run_scheduler, "start_run"),
        "authentication": (runs.cache_service, "get_cached_user"),
    }[slow_stage]
    original = getattr(target, name)

    def slow_call(*args, **kwargs):
        with gate:
            first = not entered.is_set()
            if first:
                entered.set()
        if first:
            assert release.wait(10), "Slow submission was not released"
        return original(*args, **kwargs)

    with httpx.Client(base_url=live_api, timeout=12) as client, ThreadPoolExecutor(2) as pool:
        assert client.get(f"/runs/{run.id}", headers=authenticated_headers[str(bob.id)]).status_code == 200
        monkeypatch.setattr(target, name, slow_call)
        submission = pool.submit(client.post, "/runs", headers=authenticated_headers[str(alice.id)],
                                 json={"prompt": "first single prompt"})

        def request():
            start = time.monotonic()
            if probe == "run-submission":
                response = client.post("/runs", headers=authenticated_headers[str(bob.id)],
                                       json={"prompt": "second single prompt"})
            else:
                response = client.get(f"/runs/{run.id}", headers=authenticated_headers[str(bob.id)])
            return response, time.monotonic() - start

        try:
            assert entered.wait(3), "Submission did not reach the controlled slow operation"
            pending = pool.submit(request)
            try:
                pending.result(timeout=1)
                responsive = True
            except TimeoutError:
                responsive = False
        finally:
            release.set()
        response, latency = pending.result(timeout=5)
        submission_response = submission.result(timeout=5)

    metrics(request_latency_seconds=latency, completed_while_submission_blocked=responsive,
            observation_window_seconds=1, probe=probe, slow_stage=slow_stage)
    assert submission_response.status_code == 200, submission_response.text
    assert response.status_code == 200, response.text
    assert responsive, f"User B's request waited for user A's submission ({slow_stage})"


@pytest.mark.parametrize("at_capacity", [False, True])
def test_single_prompts_remain_queued_until_worker_claim(
    live_api, authenticated_headers, two_users, make_run, monkeypatch, at_capacity,
):
    from server.database import SessionLocal
    from server.models.conversation import Conversation
    from server.models.run import Run
    from server.models.run_dispatch import RunDispatch
    from server.services import run_scheduler

    alice, bob = two_users
    run_scheduler.run_scheduler.set_max_concurrent(5)
    if at_capacity:
        for _ in range(5):
            make_run(alice, status="running")
    dispatched = []
    monkeypatch.setattr(run_scheduler, "start_run", lambda run_id, token: dispatched.append((run_id, token)))
    expected = "queued"

    with httpx.Client(base_url=live_api, timeout=8) as client, ThreadPoolExecutor(2) as pool:
        pending = [pool.submit(client.post, "/runs", headers=authenticated_headers[str(user.id)],
                               json={"prompt": f"prompt from {user.username}", "note": "  run note  "})
                   for user in two_users]
        responses = [future.result(timeout=5) for future in pending]
        ids = []
        for user, response in zip(two_users, responses):
            assert response.status_code == 200, response.text
            data = response.json()
            ids.append(data["id"])
            assert data["status"] == expected
            assert data["user_id"] == str(user.id)
            assert client.get(f"/runs/{data['id']}", headers=authenticated_headers[str(user.id)]).status_code == 200
            stranger = bob if user.id == alice.id else alice
            assert client.get(f"/runs/{data['id']}", headers=authenticated_headers[str(stranger.id)]).status_code in (403, 404)

    assert len(set(ids)) == 2
    assert sorted(run_id for run_id, _ in dispatched) == ([] if at_capacity else sorted(ids))
    with SessionLocal() as db:
        assert db.query(Conversation).count() == 0
        for user, run_id in zip(two_users, ids):
            run = db.get(Run, run_id)
            assert run.user_id == user.id
            assert run.status == expected
            dispatch = db.get(RunDispatch, run_id)
            assert dispatch.state == ("pending" if at_capacity else "dispatched")
            assert bool(dispatch.dispatch_token) is not at_capacity
            assert run.prompt == f"prompt from {user.username}"
            assert run.note == "run note"
            inputs = Path(run.extra_metadata["paths"]["inputs"])
            assert (inputs / "prompt.txt").read_text() == run.prompt
            assert (inputs / "note.txt").read_text() == "run note"

    if not at_capacity:
        from server.services.run_execution import claim_run
        with httpx.Client(base_url=live_api, timeout=8) as client:
            for run_id, token in dispatched:
                lease = claim_run(run_id, token)
                assert lease is not None
                owner = next(user for user, candidate in zip(two_users, ids) if candidate == run_id)
                response = client.get(f"/runs/{run_id}", headers=authenticated_headers[str(owner.id)])
                assert response.status_code == 200
                assert response.json()["status"] == "running"
                assert lease.finish("completed")


@pytest.mark.parametrize("rejection", ["missing-token", "invalid-token", "expired-session", "inactive-session", "inactive-user", "provider-denied", "invalid-body"])
def test_rejected_submission_does_not_create_or_dispatch_run(
    live_api, authenticated_headers, two_users, monkeypatch, rejection,
):
    from server.database import SessionLocal
    from server.models.run import Run
    from server.models.session import Session
    from server.models.user import User
    from server.services import run_scheduler

    user = two_users[0]
    request_headers = authenticated_headers[str(user.id)]
    payload = {"prompt": "single prompt"}
    expected = 401
    if rejection == "missing-token":
        request_headers = {}
    elif rejection == "invalid-token":
        request_headers = {"Authorization": "Bearer invalid"}
    elif rejection == "invalid-body":
        payload = {}
        expected = 422

    with SessionLocal() as db:
        before = db.query(Run).count()
        if rejection in ("expired-session", "inactive-session"):
            session = db.query(Session).filter(Session.user_id == user.id).one()
            if rejection == "expired-session":
                session.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            else:
                session.is_active = False
        elif rejection == "inactive-user":
            db.get(User, user.id).is_active = False
            expected = 403
        elif rejection == "provider-denied":
            db.get(User, user.id).extra_metadata = {"boltz_settings": {"provider": "self_hosted"}}
            expected = 403
        db.commit()

    dispatched = []
    monkeypatch.setattr(run_scheduler, "start_run", lambda run_id, token: dispatched.append((run_id, token)))
    with httpx.Client(base_url=live_api, timeout=8) as client:
        response = client.post("/runs", headers=request_headers, json=payload)
    assert response.status_code == expected, response.text
    assert not dispatched
    with SessionLocal() as db:
        assert db.query(Run).count() == before
