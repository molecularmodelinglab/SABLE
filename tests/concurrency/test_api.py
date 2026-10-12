"""Two-user requests through real HTTP/ASGI routing, with controlled slow providers."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading
import time
from types import SimpleNamespace

import httpx
import pytest


def headers(user):
    return {"X-Test-User": str(user.id)}


@pytest.mark.parametrize("probe", ["run-details", "run-submission"])
def test_slow_chat_does_not_block_other_user(
    live_api, two_users, make_run, monkeypatch, metrics, probe
):
    from server.routers import conversations
    from server.services import run_scheduler

    alice, bob = two_users
    run = make_run(bob)
    entered, release = threading.Event(), threading.Event()

    def slow_conversation(**kwargs):
        entered.set()
        assert release.wait(5), "Slow provider was not released"
        return SimpleNamespace(id="controlled-conversation", state="greeting", context={}), "Hello", []

    monkeypatch.setattr(conversations.conversation_service, "start_conversation", slow_conversation)
    monkeypatch.setattr(run_scheduler, "start_run", lambda run_id: None)
    with httpx.Client(base_url=live_api, timeout=8) as client, ThreadPoolExecutor(2) as pool:
        # Warm up the ordinary request path before introducing contention.
        assert client.get(f"/runs/{run.id}", headers=headers(bob)).status_code == 200
        chat = pool.submit(client.post, "/conversations", headers=headers(alice),
                           json={"initial_message": "hello"})
        assert entered.wait(3), "Conversation did not reach the simulated provider"

        def request():
            start = time.monotonic()
            if probe == "run-submission":
                response = client.post("/runs", headers=headers(bob), json={"prompt": "test request"})
            else:
                response = client.get(f"/runs/{run.id}", headers=headers(bob))
            return response, time.monotonic() - start

        pending = pool.submit(request)
        try:
            pending.result(timeout=1)
            responsive = True
        except TimeoutError:
            responsive = False
        finally:
            release.set()
        response, latency = pending.result(timeout=5)
        chat_response = chat.result(timeout=5)
    metrics(request_latency_seconds=latency, completed_while_provider_blocked=responsive,
            observation_window_seconds=1, probe=probe)
    assert chat_response.status_code == 200, chat_response.text
    assert response.status_code == 200, response.text
    assert responsive, "User B's request waited for user A's slow conversation"


def test_two_users_cannot_read_each_others_progress_or_artifacts(live_api, two_users, make_run):
    alice, bob = two_users
    first, second = make_run(alice), make_run(bob)
    with httpx.Client(base_url=live_api) as client:
        for owner, stranger, run in ((alice, bob, first), (bob, alice, second)):
            assert client.get(f"/runs/{run.id}", headers=headers(owner)).status_code == 200
            for suffix in ("events", "artifacts/summary.txt"):
                response = client.get(f"/runs/{run.id}/{suffix}", headers=headers(stranger))
                assert response.status_code in (403, 404)
