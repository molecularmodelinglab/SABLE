"""Real stream iterators, shared AnyIO capacity, and independent worker delivery."""
import asyncio
import json
import queue
import subprocess
import sys
import time

import anyio
import httpx

def test_more_than_40_idle_streams_leave_request_threads_free(api, two_users, make_run, metrics):
    from server.database import get_db_context
    from server.routers.runs import sse_events
    from server.services.run_events import run_event_hub

    alice, bob = two_users
    first, second = make_run(alice), make_run(bob)

    async def scenario():
        limiter = anyio.to_thread.current_default_thread_limiter()
        assert limiter.total_tokens == 40, "Exercise the production-default request thread capacity"
        streams = []
        for index in range(44):
            user, run = (alice, first) if index % 2 else (bob, second)
            with get_db_context() as db:
                response = sse_events(run.id, user, db)
                if asyncio.iscoroutine(response):
                    response = await response
            assert response.media_type == "text/event-stream", "Do not use the testing SSE bypass"
            streams.append(response.body_iterator)
        for stream in streams:
            assert "event: hello" in await anext(stream)
        waiting = [asyncio.create_task(anext(stream)) for stream in streams]
        await asyncio.sleep(0.2)
        occupied = limiter.borrowed_tokens
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
            async def request():
                start = time.monotonic()
                response = await client.get(f"/runs/{second.id}", headers={"X-Test-User": str(bob.id)})
                return response, time.monotonic() - start

            probe = asyncio.create_task(request())
            try:
                await asyncio.wait_for(asyncio.shield(probe), timeout=1)
                responsive = True
            except asyncio.TimeoutError:
                responsive = False
            finally:
                # Wake every blocked queue before cancellation/closing generators.
                for run in (first, second):
                    run_event_hub.append_log(run.id, {"message": "test cleanup"})
                await asyncio.wait_for(asyncio.gather(*waiting), timeout=5)
                response, latency = await asyncio.wait_for(probe, timeout=5)
                for stream in streams:
                    await stream.aclose()
            metrics(idle_streams=len(streams), occupied_thread_slots=occupied,
                    request_latency_seconds=latency, request_completed_while_idle=responsive,
                    remaining_subscriptions=sum(len(run_event_hub._get_subscribers(run.id))
                                                for run in (first, second)))
            assert response.status_code == 200
            assert not run_event_hub._get_subscribers(first.id)
            assert not run_event_hub._get_subscribers(second.id)
            assert responsive, "Idle SSE streams exhausted another user's request capacity"
            assert occupied == 0, "Idle SSE streams must not occupy shared request threads"
    asyncio.run(scenario())


def test_worker_events_reach_api_subscriber_across_processes(two_users, make_run, metrics):
    from server.services.run_events import run_event_hub
    from server.storage import run_dir

    run = make_run(two_users[0])
    received = queue.Queue()
    run_event_hub.subscribe(run.id, received.put)
    event = {"message": "independent worker event", "sequence": 1}
    start = time.monotonic()
    try:
        # A fresh interpreter cannot accidentally inherit the API's subscriber state.
        worker = subprocess.run(
            [sys.executable, "-c",
             "import json, sys; from server.services.run_events import run_event_hub; "
             "run_event_hub.append_log(sys.argv[1], json.loads(sys.argv[2]))",
             run.id, json.dumps(event)], capture_output=True, text=True, timeout=5,
        )
        assert worker.returncode == 0, worker.stderr
        persisted = (run_dir(run.id) / "logs" / "logs.ndjson").read_text()
        assert json.loads(persisted.splitlines()[-1]) == event
        try:
            delivered = received.get(timeout=2)
        except queue.Empty:
            delivered = None
        metrics(event_delivery_seconds=time.monotonic() - start,
                cross_process_event_delivered=delivered is not None)
        assert delivered == event, "Worker persisted progress but API subscriber never received it"
    finally:
        run_event_hub.unsubscribe(run.id, received.put)


def test_two_user_progress_isolated_and_unsubscribed(two_users, make_run, metrics):
    from server.services.run_events import run_event_hub

    first, second = (make_run(user) for user in two_users)
    alice, bob = [], []
    run_event_hub.subscribe(first.id, alice.append)
    run_event_hub.subscribe(second.id, bob.append)
    try:
        run_event_hub.append_log(first.id, {"message": "alice"})
        run_event_hub.append_log(second.id, {"message": "bob"})
    finally:
        run_event_hub.unsubscribe(first.id, alice.append)
        run_event_hub.unsubscribe(second.id, bob.append)
    run_event_hub.append_log(first.id, {"message": "after unsubscribe"})
    metrics(remaining_subscriptions=sum(len(run_event_hub._get_subscribers(run.id))
                                       for run in (first, second)))
    assert alice == [{"message": "alice"}]
    assert bob == [{"message": "bob"}]
