"""Isolated Redis/PostgreSQL acceptance tests for live-only async run events."""
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import anyio
import httpx
import pytest
import redis


@pytest.fixture
def event_redis():
    client = redis.Redis.from_url(os.environ["REDIS_URL"])
    assert client.ping(), "The dedicated Redis instance is required"
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def hub_factory():
    from server.services.run_events import RunEventHub

    hubs = []

    def create(**kwargs):
        kwargs.setdefault("redis_url", os.environ["REDIS_URL"])
        hub = RunEventHub(**kwargs)
        hubs.append(hub)
        return hub

    yield create
    for hub in hubs:
        hub._publisher.close()


def channel(run_id, db=0):
    return f"sable:run-events:{db}:{run_id}"


async def subscription_counts(client, *run_ids):
    rows = await asyncio.to_thread(client.pubsub_numsub, *(channel(r) for r in run_ids))
    return [int(count) for _, count in rows]


async def wait_unsubscribed(client, *run_ids):
    async with asyncio.timeout(3):
        while any(await subscription_counts(client, *run_ids)):
            await asyncio.sleep(0.01)


async def cancel_tasks(tasks):
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def frame_text(frame):
    return frame.decode("utf-8") if isinstance(frame, bytes) else frame


def frame_data(frame):
    return json.loads("\n".join(line[5:].lstrip() for line in frame_text(frame).splitlines()
                                if line.startswith("data:")))


def stream_for(run, user):
    from server.database import SessionLocal
    from server.routers.runs import sse_events
    from starlette.responses import StreamingResponse

    with SessionLocal() as db:
        response = sse_events(run.id, user, db)
        assert isinstance(response, StreamingResponse), "The route must remain synchronous"
        assert response.media_type == "text/event-stream", "Do not use the testing SSE bypass"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        return response


def test_more_than_40_idle_streams_leave_request_threads_free(
    api, two_users, make_run, metrics, event_redis, monkeypatch
):
    from server.services.run_events import run_event_hub

    alice, bob = two_users
    first, second = make_run(alice), make_run(bob)
    monkeypatch.setattr(run_event_hub, "heartbeat_seconds", 60)

    async def scenario():
        limiter = anyio.to_thread.current_default_thread_limiter()
        assert limiter.total_tokens == 40, "Exercise the production-default request thread capacity"
        streams, waiting = [], []
        try:
            for index in range(44):
                user, run = (alice, first) if index % 2 else (bob, second)
                stream = stream_for(run, user).body_iterator
                streams.append(stream)
                assert "event: hello" in frame_text(await asyncio.wait_for(anext(stream), 3))
            assert await subscription_counts(event_redis, first.id, second.id) == [22, 22]
            waiting = [asyncio.create_task(anext(stream)) for stream in streams]
            await asyncio.sleep(0.2)
            assert not any(task.done() for task in waiting), "Streams should be idle, not closed"
            occupied = limiter.borrowed_tokens
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                         base_url="http://test") as client:
                start = time.monotonic()
                response = await asyncio.wait_for(
                    client.get(f"/runs/{second.id}", headers={"X-Test-User": str(bob.id)}), 1
                )
                latency = time.monotonic() - start
            assert response.status_code == 200
            assert occupied == 0, "Idle SSE streams must not occupy shared request threads"
        finally:
            await cancel_tasks(waiting)
            for stream in streams:
                await stream.aclose()
            await wait_unsubscribed(event_redis, first.id, second.id)
        metrics(idle_streams=len(streams), occupied_thread_slots=occupied,
                request_latency_seconds=latency, request_completed_while_idle=True,
                remaining_subscriptions=sum(await subscription_counts(event_redis, first.id, second.id)))

    asyncio.run(scenario())


def test_worker_events_reach_api_subscriber_across_processes(
    two_users, make_run, metrics, event_redis
):
    from server.services.run_events import run_event_hub
    from server.storage import run_dir

    run = make_run(two_users[0])
    event = {"message": "independent worker event", "sequence": 1}

    async def scenario():
        start = time.monotonic()
        async with run_event_hub.subscribe(run.id) as received:
            assert isinstance(received, asyncio.Queue)
            assert await subscription_counts(event_redis, run.id) == [1]
            pending = asyncio.create_task(received.get())
            await asyncio.sleep(0)
            try:
                # A fresh interpreter cannot inherit subscriber state. Keep the reader loop running.
                worker = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-c",
                     "import json, sys; from server.services.run_events import run_event_hub; "
                     "run_event_hub.append_log(sys.argv[1], json.loads(sys.argv[2]))",
                     run.id, json.dumps(event)], capture_output=True, text=True, timeout=10,
                )
                assert worker.returncode == 0, worker.stderr
                delivered = await asyncio.wait_for(pending, 3)
            finally:
                await cancel_tasks([pending])
            persisted = (run_dir(run.id) / "logs" / "logs.ndjson").read_text(encoding="utf-8")
            assert json.loads(persisted.splitlines()[-1]) == event
            metrics(event_delivery_seconds=time.monotonic() - start,
                    cross_process_event_delivered=delivered == event)
            assert delivered == event, "Worker persisted progress but API subscriber never received it"
        await wait_unsubscribed(event_redis, run.id)

    asyncio.run(scenario())


def test_two_user_progress_isolated_and_unsubscribed(two_users, make_run, metrics, event_redis):
    from server.services.run_events import run_event_hub

    first, second = (make_run(user) for user in two_users)

    async def scenario():
        async with AsyncExitStack() as stack:
            alice = await stack.enter_async_context(run_event_hub.subscribe(first.id))
            bob = await stack.enter_async_context(run_event_hub.subscribe(second.id))
            assert await subscription_counts(event_redis, first.id, second.id) == [1, 1]
            run_event_hub.append_log(first.id, {"message": "alice"})
            run_event_hub.append_log(second.id, {"message": "bob"})
            assert await asyncio.wait_for(alice.get(), 3) == {"message": "alice"}
            assert await asyncio.wait_for(bob.get(), 3) == {"message": "bob"}
        await wait_unsubscribed(event_redis, first.id, second.id)
        run_event_hub.append_log(first.id, {"message": "after unsubscribe"})
        run_event_hub.append_log(second.id, {"message": "after unsubscribe"})
        await asyncio.sleep(0.05)
        for events in (alice, bob):
            while not events.empty():
                assert events.get_nowait() is None, "Unsubscribed queues received more progress"
        metrics(remaining_subscriptions=sum(await subscription_counts(event_redis, first.id, second.id)))

    asyncio.run(scenario())


def test_subscribe_ack_and_live_only_reconnect_ignore_invalid_messages(
    two_users, make_run, event_redis, hub_factory
):
    hub = hub_factory()
    run = make_run(two_users[0])

    async def scenario():
        hub.append_log(run.id, {"message": "before first subscription"})
        for sequence in range(2):
            async with hub.subscribe(run.id) as received:
                assert received.maxsize == 256
                # No sleep/retry: __aenter__ promises the Redis SUBSCRIBE acknowledgment.
                assert await subscription_counts(event_redis, run.id) == [1]
                for invalid in ("not-json", "[]", "null", '"string"', "42"):
                    event_redis.publish(channel(run.id), invalid)
                live = {"message": "live", "sequence": sequence}
                hub.append_log(run.id, live)
                assert await asyncio.wait_for(received.get(), 3) == live
                assert received.empty()
            await wait_unsubscribed(event_redis, run.id)
            hub.append_log(run.id, {"message": "while disconnected"})

    asyncio.run(scenario())


def test_independent_hubs_namespace_pubsub_by_redis_database(
    two_users, make_run, event_redis, hub_factory
):
    run = make_run(two_users[0])
    first = hub_factory()
    second = hub_factory(redis_url=os.environ["REDIS_URL"].rsplit("/", 1)[0] + "/1")
    assert first._channel(run.id) == channel(run.id, db=0)
    assert second._channel(run.id) == channel(run.id, db=1)

    async def scenario():
        async with first.subscribe(run.id) as one, second.subscribe(run.id) as two:
            counts = event_redis.pubsub_numsub(channel(run.id, db=0), channel(run.id, db=1))
            assert [count for _, count in counts] == [1, 1]
            first.append_log(run.id, {"deployment": "first"})
            second.append_log(run.id, {"deployment": "second"})
            assert await asyncio.wait_for(one.get(), 3) == {"deployment": "first"}
            assert await asyncio.wait_for(two.get(), 3) == {"deployment": "second"}
            assert one.empty() and two.empty()
        async with asyncio.timeout(3):
            while any(count for _, count in event_redis.pubsub_numsub(
                channel(run.id, db=0), channel(run.id, db=1)
            )):
                await asyncio.sleep(0.01)

    asyncio.run(scenario())


def test_publish_failure_preserves_json_safe_ndjson(two_users, make_run, hub_factory, monkeypatch):
    from redis.exceptions import RedisError
    from server.storage import run_dir

    class State(Enum):
        RUNNING = "running"

    hub = hub_factory()
    run = make_run(two_users[0])
    when = datetime(2026, 1, 2, tzinfo=timezone.utc)
    event = {"message": "progress", "nested": {"state": State.RUNNING, "at": when,
                                             "path": Path("artifact.txt"), "items": (1, 2)}}
    expected = {"message": "progress", "nested": {"state": "running", "at": when.isoformat(),
                                                "path": "artifact.txt", "items": [1, 2]}}
    attempts = []
    log_path = run_dir(run.id) / "logs" / "logs.ndjson"

    def unavailable(topic, payload, *args, **kwargs):
        attempts.append((topic, json.loads(payload)))
        # Durability precedes notification, even while Redis is failing.
        assert json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1]) == json.loads(payload)
        raise RedisError("controlled publish failure")

    monkeypatch.setattr(hub._publisher, "publish", unavailable)
    assert hub.append_log(run.id, event) is None
    assert hub.append_log(run.id, {"message": "workflow continues"}) is None
    assert [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()] == [
        expected, {"message": "workflow continues"}
    ]
    assert attempts == [(channel(run.id), expected), (channel(run.id), {"message": "workflow continues"})]


def test_slow_subscription_buffer_is_bounded_and_signals_close(
    two_users, make_run, event_redis, hub_factory
):
    hub = hub_factory(buffer_size=2)
    run = make_run(two_users[0])

    async def scenario():
        baseline = asyncio.all_tasks()
        async with hub.subscribe(run.id) as slow:
            readers = asyncio.all_tasks() - baseline
            assert readers, "A subscription needs an independent reader task"
            assert slow.maxsize == 2
            for sequence in range(20):
                hub.append_log(run.id, {"sequence": sequence})
            # Wait for overflow without draining the queue or depending on a fixed sleep.
            _, pending = await asyncio.wait(readers, timeout=3)
            assert not pending, "Overflow must stop the slow subscriber's reader"
            assert slow.qsize() <= 2
            values = []
            for _ in range(3):
                value = await asyncio.wait_for(slow.get(), 3)
                values.append(value)
                if value is None:
                    break
            assert values[-1] is None, "Overflow must close/reconnect, not silently drop progress"
        await wait_unsubscribed(event_redis, run.id)

    asyncio.run(scenario())


def test_each_subscription_releases_reader_and_redis_connection_on_cancellation(
    two_users, make_run, event_redis, hub_factory
):
    hub = hub_factory()
    run = make_run(two_users[0])

    async def scenario():
        baseline_ids = {row["id"] for row in event_redis.client_list()}
        baseline_tasks = asyncio.all_tasks()
        ready = [asyncio.Event(), asyncio.Event()]

        async def consume(index):
            async with hub.subscribe(run.id) as events:
                ready[index].set()
                await events.get()

        consumers = [asyncio.create_task(consume(index)) for index in range(2)]
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready)), 3)
            assert await subscription_counts(event_redis, run.id) == [2]
            connected = {row["id"] for row in event_redis.client_list()} - baseline_ids
            assert len(connected) == 2, "Every subscription must own a separate Redis connection"
            reader_tasks = asyncio.all_tasks() - baseline_tasks - set(consumers)
            assert len(reader_tasks) >= 2
        finally:
            await cancel_tasks(consumers)
        await wait_unsubscribed(event_redis, run.id)
        async with asyncio.timeout(3):
            while connected & {row["id"] for row in event_redis.client_list()}:
                await asyncio.sleep(0.01)
        assert all(task.done() for task in reader_tasks), "Reader tasks leaked after cancellation"

    asyncio.run(scenario())


def test_subscribe_waits_for_acknowledgment(two_users, make_run, hub_factory, monkeypatch):
    from redis.asyncio.client import PubSub

    hub = hub_factory()
    run = make_run(two_users[0])
    original = PubSub.parse_response

    async def scenario():
        acknowledged, release, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def delayed_ack(self, *args, **kwargs):
            response = await original(self, *args, **kwargs)
            if response and response[0] in (b"subscribe", "subscribe"):
                acknowledged.set()
                await release.wait()
            return response

        monkeypatch.setattr(PubSub, "parse_response", delayed_ack)

        async def subscribe():
            async with hub.subscribe(run.id):
                entered.set()

        pending = asyncio.create_task(subscribe())
        try:
            await asyncio.wait_for(acknowledged.wait(), 3)
            assert not entered.is_set(), "Subscription returned before its acknowledgment"
            release.set()
            await asyncio.wait_for(pending, 3)
            assert entered.is_set()
        finally:
            release.set()
            await cancel_tasks([pending])

    asyncio.run(scenario())


def test_sse_hello_live_event_heartbeat_and_no_replay(two_users, make_run, event_redis, monkeypatch):
    from server.services.run_events import run_event_hub

    user = two_users[0]
    run = make_run(user)
    monkeypatch.setattr(run_event_hub, "heartbeat_seconds", 0.05)

    async def next_data(stream):
        async with asyncio.timeout(3):
            while True:
                frame = frame_text(await anext(stream))
                if frame.startswith("data:"):
                    return frame_data(frame)

    async def scenario():
        run_event_hub.append_log(run.id, {"message": "historical"})
        for sequence in range(2):
            stream = stream_for(run, user).body_iterator
            try:
                hello = frame_text(await asyncio.wait_for(anext(stream), 3))
                assert "event: hello" in hello
                assert "retry: 3000" in hello.splitlines()
                assert frame_data(hello) == {"run_id": run.id}
                assert await subscription_counts(event_redis, run.id) == [1]
                assert frame_text(await asyncio.wait_for(anext(stream), 3)) == ": heartbeat\n\n"
                event = {"message": "live", "sequence": sequence}
                run_event_hub.append_log(run.id, event)
                assert await next_data(stream) == event
            finally:
                await stream.aclose()
            await wait_unsubscribed(event_redis, run.id)
            run_event_hub.append_log(run.id, {"message": "offline"})

    asyncio.run(scenario())


def test_sse_authorizes_and_closes_database_before_subscribing(
    two_users, make_run, event_redis, monkeypatch
):
    from contextlib import asynccontextmanager
    from server.database import SessionLocal, engine
    from server.routers import runs

    user = two_users[0]
    run = make_run(user)
    original_subscribe = runs.run_event_hub.subscribe
    original_authorize = runs.check_run_authorization
    order = []

    def authorize(*args, **kwargs):
        result = original_authorize(*args, **kwargs)
        order.append("authorized")
        return result

    async def scenario():
        baseline = engine.pool.checkedout()
        db = SessionLocal()
        close = db.close

        def close_db():
            order.append("closed")
            close()

        @asynccontextmanager
        async def subscribe(run_id):
            assert order[:2] == ["authorized", "closed"]
            assert not db.in_transaction()
            assert engine.pool.checkedout() == baseline
            order.append("subscribed")
            async with original_subscribe(run_id) as events:
                yield events

        monkeypatch.setattr(runs, "check_run_authorization", authorize)
        monkeypatch.setattr(db, "close", close_db)
        monkeypatch.setattr(runs.run_event_hub, "subscribe", subscribe)
        stream = None
        try:
            response = runs.sse_events(run.id, user, db)
            assert order == ["authorized", "closed"], "Do not hold DB sessions for idle streams"
            stream = response.body_iterator
            assert "event: hello" in frame_text(await asyncio.wait_for(anext(stream), 3))
            assert order == ["authorized", "closed", "subscribed"]
        finally:
            if stream is not None:
                await stream.aclose()
            close()
        await wait_unsubscribed(event_redis, run.id)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["subscribe", "read"])
def test_sse_redis_failure_ends_stream_and_releases_resources(
    failure, two_users, make_run, event_redis, hub_factory, monkeypatch
):
    from redis.asyncio.client import PubSub
    from redis.exceptions import ConnectionError
    from server.routers import runs

    hub = hub_factory(heartbeat_seconds=60)
    monkeypatch.setattr(runs, "run_event_hub", hub)
    run = make_run(two_users[0])
    original = PubSub.parse_response
    failing = failure == "subscribe"

    async def controlled_read(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if failing:
            raise ConnectionError("controlled Redis read failure")
        return result

    monkeypatch.setattr(PubSub, "parse_response", controlled_read)

    async def scenario():
        nonlocal failing
        hub._publisher.ping()
        baseline_ids = {row["id"] for row in event_redis.client_list()}
        baseline_tasks = asyncio.all_tasks()
        stream = stream_for(run, two_users[0]).body_iterator
        try:
            if failure == "read":
                assert "event: hello" in frame_text(await asyncio.wait_for(anext(stream), 3))
                failing = True
                hub.append_log(run.id, {"message": "wake blocked read"})
            frames = []
            async with asyncio.timeout(3):
                async for frame in stream:
                    frames.append(frame_text(frame))
            if failure == "subscribe":
                assert frames, "Initial connection failure must advise EventSource to retry"
                assert any("retry: 3000" in frame.splitlines() for frame in frames)
                assert not any("event: hello" in frame for frame in frames)
            assert not any('"message"' in frame for frame in frames)
        finally:
            await stream.aclose()
        await wait_unsubscribed(event_redis, run.id)
        async with asyncio.timeout(3):
            while {row["id"] for row in event_redis.client_list()} - baseline_ids:
                await asyncio.sleep(0.01)
        assert not (asyncio.all_tasks() - baseline_tasks), "Redis failure leaked a reader task"

    asyncio.run(scenario())


def test_sse_initial_connect_failure_sends_retry(two_users, make_run, hub_factory, monkeypatch):
    from redis.asyncio.client import PubSub
    from redis.exceptions import ConnectionError
    from server.routers import runs

    monkeypatch.setattr(runs, "run_event_hub", hub_factory())
    run = make_run(two_users[0])

    async def unavailable(*args, **kwargs):
        raise ConnectionError("controlled initial Redis connection failure")

    monkeypatch.setattr(PubSub, "connect", unavailable)

    async def scenario():
        stream = stream_for(run, two_users[0]).body_iterator
        try:
            frames = []
            async with asyncio.timeout(3):
                async for frame in stream:
                    frames.append(frame_text(frame))
            assert any("retry: 3000" in frame.splitlines() for frame in frames)
            assert not any("event: hello" in frame for frame in frames)
        finally:
            await stream.aclose()

    asyncio.run(scenario())


def test_sse_overflow_closes_slow_stream(two_users, make_run, event_redis, hub_factory, monkeypatch):
    from server.routers import runs

    hub = hub_factory(buffer_size=2, heartbeat_seconds=60)
    monkeypatch.setattr(runs, "run_event_hub", hub)
    run = make_run(two_users[0])

    async def scenario():
        baseline = asyncio.all_tasks()
        stream = stream_for(run, two_users[0]).body_iterator
        try:
            assert "event: hello" in frame_text(await asyncio.wait_for(anext(stream), 3))
            readers = asyncio.all_tasks() - baseline
            assert readers
            for sequence in range(20):
                hub.append_log(run.id, {"sequence": sequence})
            _, pending = await asyncio.wait(readers, timeout=3)
            assert not pending, "The slow stream reader did not stop after overflow"
            frames = []
            async with asyncio.timeout(3):
                async for frame in stream:
                    frames.append(frame)
            assert len(frames) <= 3, "A stalled stream must not accumulate an unbounded backlog"
        finally:
            await stream.aclose()
        await wait_unsubscribed(event_redis, run.id)

    asyncio.run(scenario())


def test_real_http_ownership_and_disconnect_cleanup(live_api, two_users, make_run, event_redis):
    from server.database import engine

    alice, bob = two_users
    first, second = make_run(alice), make_run(bob)

    async def scenario():
        baseline = engine.pool.checkedout()
        async with httpx.AsyncClient(base_url=live_api, timeout=3) as client:
            for owner, stranger, run in ((alice, bob, first), (bob, alice, second)):
                response = await client.get(f"/runs/{run.id}/events",
                                            headers={"X-Test-User": str(stranger.id)})
                assert response.status_code in (403, 404)
                assert await subscription_counts(event_redis, run.id) == [0]
                async with client.stream("GET", f"/runs/{run.id}/events",
                                         headers={"X-Test-User": str(owner.id)}) as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith("text/event-stream")
                    assert response.headers["cache-control"] == "no-cache"
                    assert response.headers["x-accel-buffering"] == "no"
                    lines = response.aiter_lines()
                    hello = []
                    async with asyncio.timeout(3):
                        async for line in lines:
                            if not line:
                                break
                            hello.append(line)
                    assert "event: hello" in hello
                    assert "retry: 3000" in hello
                    assert await subscription_counts(event_redis, run.id) == [1]
                    assert engine.pool.checkedout() == baseline, "HTTP SSE retained a DB connection"
                # Closing the real socket must release Redis without publishing a cleanup event.
                await wait_unsubscribed(event_redis, run.id)

    asyncio.run(scenario())


@pytest.mark.parametrize("termination", ["send-timeout", "disconnect", "cancel"])
def test_event_stream_response_shields_iterator_cleanup(termination):
    from server.services.sse import EventStreamResponse
    from starlette.responses import StreamingResponse

    async def scenario():
        sending, cleaned = asyncio.Event(), asyncio.Event()

        async def body():
            try:
                yield "data: progress\n\n"
                await anyio.sleep_forever()
            finally:
                # A checkpoint proves cleanup survived an already-cancelled AnyIO scope.
                await anyio.sleep(0.01)
                cleaned.set()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                sending.set()
                await anyio.sleep_forever()

        async def receive():
            await sending.wait()
            if termination == "disconnect":
                return {"type": "http.disconnect"}
            await anyio.sleep_forever()

        response = EventStreamResponse(body(),
                                       send_timeout=0.05 if termination == "send-timeout" else 15)
        assert isinstance(response, StreamingResponse)
        scope = {"type": "http", "method": "GET", "path": "/events", "headers": [],
                 "asgi": {"version": "3.0", "spec_version": "2.3"}}
        pending = asyncio.create_task(response.stream_response(send) if termination == "send-timeout"
                                      else response(scope, receive, send))
        try:
            await asyncio.wait_for(sending.wait(), 3)
            if termination == "cancel":
                pending.cancel()
            done, _ = await asyncio.wait({pending}, timeout=2)
            assert done, "An unresponsive client must not hold streaming resources indefinitely"
            result, = await asyncio.gather(pending, return_exceptions=True)
            if termination == "send-timeout":
                assert isinstance(result, TimeoutError)
            elif termination == "disconnect":
                assert result is None
            else:
                assert isinstance(result, asyncio.CancelledError)
            assert cleaned.is_set(), "Response returned without shielded async iterator cleanup"
        finally:
            await cancel_tasks([pending])

    asyncio.run(scenario())


@pytest.mark.parametrize("blocked_message", ["start", "end"])
def test_event_stream_response_bounds_header_and_final_sends(blocked_message):
    from server.services.sse import EventStreamResponse

    async def scenario():
        cleaned = asyncio.Event()

        class EmptyIterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def aclose(self):
                await anyio.sleep(0.01)
                cleaned.set()

        async def send(message):
            if ((blocked_message == "start" and message["type"] == "http.response.start")
                    or (blocked_message == "end" and message["type"] == "http.response.body")):
                await anyio.sleep_forever()

        response = EventStreamResponse(EmptyIterator(), send_timeout=0.05)
        async with asyncio.timeout(2):
            with pytest.raises(TimeoutError):
                await response.stream_response(send)
        assert cleaned.is_set(), "Even an unstarted/exhausted iterator must be explicitly closed"

    asyncio.run(scenario())
