"""Production-shaped acceptance, never deployment or existing-run mutation.

Uses default 15s heartbeat / 90s execution lease / 5s reconciliation. The only
execution replacement is the external WorkflowRunner; no claim, scheduler,
authentication, event hub, launcher, or workflow task is monkeypatched.
"""
import asyncio
from collections import Counter
import json
import math
from pathlib import Path
import time

import httpx
import pytest
from sqlalchemy import text

from multiprocess_support import BrokerProxy, Processes, unused_port


def snapshot():
    from server.database import engine
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text("""
            SELECT r.id, r.status, r.user_id::text AS user_id, r.created_at,
                   r.exit_reason, d.state, d.dispatch_token, d.execution_owner,
                   d.heartbeat_at, d.lease_expires_at, d.publish_attempts
            FROM runs r JOIN run_dispatches d ON d.run_id = r.id
            ORDER BY r.created_at, r.id
        """)).mappings()]


async def until(predicate, *, timeout=20, description="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(0.1)
    raise AssertionError(f"Timed out after {timeout}s: {description}")


@pytest.mark.timeout(280)
def test_two_apis_real_prefork_workers_and_fault_recovery(two_users, make_run, metrics):
    from server.auth.jwt import create_access_token
    from server.services.cache_service import cache_service
    from server.services import run_execution, run_scheduler, scheduler_lifecycle

    assert (run_execution.HEARTBEAT_SECONDS, run_execution.EXECUTION_LEASE_SECONDS,
            run_scheduler.RESERVATION_SECONDS, run_scheduler.PUBLICATION_LEASE_SECONDS,
            scheduler_lifecycle.RECONCILE_INTERVAL_SECONDS) == (15, 90, 300, 30, 5)
    reads = [make_run(user, status="completed") for user in two_users]
    headers = [{"Authorization": "Bearer " + create_access_token({
        "user_id": str(user.id), "session_id": str(run.session_id),
    })} for user, run in zip(two_users, reads)]
    client = cache_service.redis
    values = {"api_processes": 2, "celery_workers": 2, "prefork_concurrency_each": 3,
              "shared_limit": 5, "concurrent_submissions": 10, "users": 2,
              "idle_sse_clients": 40, "production_timings_unmodified": True,
              "heartbeat_seconds": 15, "execution_lease_seconds": 90,
              "reservation_seconds": 300, "publication_lease_seconds": 30,
              "reconcile_seconds": 5, "stage": "startup"}

    async def acceptance():
        proxy = BrokerProxy()
        broker = await proxy.start()
        processes = Processes(broker)
        streams, monitors = [], []
        rows_seen, latencies = [], {}
        all_run_ids = []
        killed_runs = []
        channel = None
        started = time.monotonic()
        try:
            ports = [unused_port(), unused_port()]
            apis = [processes.spawn("api", port) for port in ports]
            workers = [processes.spawn("worker", f"phase6-{index}@%h") for index in range(2)]
            urls = [f"http://127.0.0.1:{port}" for port in ports]
            async with httpx.AsyncClient(timeout=12, limits=httpx.Limits(
                    max_connections=120, max_keepalive_connections=60)) as http:
                async def healthy():
                    assert all(p.poll() is None for p in apis + workers), "Child exited; inspect phase6 logs"
                    try:
                        statuses = await asyncio.gather(*[http.get(url + "/phase6/health") for url in urls])
                        return all(r.status_code == 200 and r.json()["scheduler"] for r in statuses)
                    except httpx.TransportError:
                        return False

                await until(healthy, timeout=40, description="two live APIs and scheduler lifespans")

                async def worker_ready():
                    return await asyncio.to_thread(client.hlen, "phase6:workers") == 2

                await until(worker_ready, timeout=30, description="two actual Celery workers ready")
                topology = {str(worker.pid): sorted(processes.capture(worker.pid) - {worker.pid})
                            for worker in workers}
                assert all(len(children) == 3 for children in topology.values()), topology
                values.update(api_pids=[p.pid for p in apis], worker_prefork_pids=topology,
                              startup_seconds=time.monotonic() - started)
                assert (await http.get(urls[0] + "/runs")).status_code == 401
                assert (await http.get(urls[1] + f"/runs/{reads[0].id}", headers=headers[1])).status_code in (403, 404)

                ready = [asyncio.Event() for _ in range(40)]

                async def sse(index):
                    user = index % 2
                    async with http.stream("GET", urls[(index // 2) % 2] + f"/runs/{reads[user].id}/events",
                                           headers=headers[user], timeout=None) as response:
                        assert response.status_code == 200
                        async for line in response.aiter_lines():
                            if line == "event: hello":
                                ready[index].set()

                streams.extend(asyncio.create_task(sse(index)) for index in range(40))
                await asyncio.wait_for(asyncio.gather(*[event.wait() for event in ready]), 15)

                async def reader_count():
                    responses = await asyncio.gather(*[http.get(url + "/phase6/health") for url in urls])
                    return [r.json()["event_readers"] for r in responses]

                assert await reader_count() == [20, 20]
                values["sse_readers_at_pressure"] = [20, 20]

                async def observe():
                    while True:
                        current = await asyncio.to_thread(snapshot)
                        row = {"time": time.time(), "stage": values["stage"],
                               "slots": sum(r["state"] in {"reserved", "dispatched", "running"} for r in current),
                               "running": sum(r["status"] == "running" for r in current)}
                        rows_seen.append(row)
                        await asyncio.sleep(0.1)

                async def read_pressure():
                    index = 0
                    while True:
                        stage = values["stage"]
                        start = time.monotonic()
                        response = await http.get(urls[index % 2] + f"/runs/{reads[(index // 2) % 2].id}",
                                                  headers=headers[(index // 2) % 2])
                        assert response.status_code == 200, response.text
                        latencies.setdefault(stage, []).append(time.monotonic() - start)
                        index += 1
                        await asyncio.sleep(0.03)

                monitors.extend([asyncio.create_task(observe()), asyncio.create_task(read_pressure())])
                values["stage"] = "ten-submissions-forty-idle-streams"
                barrier = asyncio.Event()

                async def submit(index, prompt):
                    await barrier.wait()
                    response = await http.post(urls[(index // 2) % 2] + "/runs", headers=headers[index % 2],
                                               json={"prompt": prompt})
                    assert response.status_code == 200, response.text
                    return response.json()["id"]

                tasks = [asyncio.create_task(submit(index, f"phase6 controlled prompt {index}"))
                         for index in range(10)]
                barrier.set()
                all_run_ids.extend(await asyncio.wait_for(asyncio.gather(*tasks), 30))
                assert len(set(all_run_ids)) == 10

                async def entry_count(number):
                    return await asyncio.to_thread(client.hlen, "phase6:entries") == number

                await until(lambda: entry_count(5), description="five gated workflow bodies")
                initial = await asyncio.to_thread(snapshot)
                running = [r for r in initial if r["status"] == "running"]
                assert len(running) == 5
                assert Counter(r["user_id"] for r in initial) == {str(two_users[0].id): 5, str(two_users[1].id): 5}

                # Deliberately deliver an existing reservation again through real Celery.
                from celery import Celery
                channel = Celery("phase6-delivery-probe", broker=broker, set_as_current=False)
                channel.conf.broker_transport_options = {"socket_timeout": 3, "socket_connect_timeout": 3}

                async def redeliver(row):
                    await asyncio.to_thread(channel.send_task, "server.tasks.workflow.run_workflow",
                                            args=[row["id"], row["dispatch_token"]], ignore_result=True, retry=False)

                await redeliver(running[0])

                async def delivered_twice():
                    return int(await asyncio.to_thread(client.hget, "phase6:deliveries", running[0]["id"]) or 0) >= 2

                await until(delivered_twice, description="duplicate real delivery refused before body entry")
                assert int(await asyncio.to_thread(client.hget, "phase6:entries", running[0]["id"])) == 1

                admitted = Counter(row["user_id"] for row in running)
                release = max(running, key=lambda row: admitted[row["user_id"]])
                admitted[release["user_id"]] -= 1
                pending = [row for row in initial if row["state"] == "pending"]
                expected = min(pending, key=lambda row: (admitted[row["user_id"]], row["created_at"], row["id"]))
                await asyncio.to_thread(client.sadd, "phase6:release", release["id"])
                await until(lambda: entry_count(6), description="least-admitted next run enters")
                owners = {key: json.loads(value) for key, value in
                          (await asyncio.to_thread(client.hgetall, "phase6:owners")).items()}
                assert expected["id"] in owners, "Least-admitted/FIFO admission order changed"
                values["fairness_next_run"] = expected["id"]
                values["stage"] = "slow-provider-heartbeat"
                await asyncio.sleep(18)
                before_kill = await asyncio.to_thread(snapshot)
                for row in running:
                    if row["id"] != release["id"]:
                        current = next(r for r in before_kill if r["id"] == row["id"])
                        assert current["status"] == "running", "Slow workflow was preempted"
                        assert current["execution_owner"] == row["execution_owner"]
                        assert current["heartbeat_at"] > row["heartbeat_at"]

                # Disconnect half the idle clients; async hub readers must be reclaimed.
                for stream in streams[:20]:
                    stream.cancel()
                await asyncio.gather(*streams[:20], return_exceptions=True)

                async def half_disconnected():
                    return await reader_count() == [10, 10]

                await until(half_disconnected, timeout=5, description="disconnected SSE readers reclaimed")
                values["sse_readers_after_disconnect"] = [10, 10]
                active = [r for r in before_kill if r["status"] == "running"]
                victim = next(w for w in workers if any(owners[r["id"]]["worker_pid"] == w.pid for r in active))
                killed_runs.extend(r["id"] for r in active if owners[r["id"]]["worker_pid"] == victim.pid)
                survivor = next(r for r in active if r["id"] not in killed_runs)
                kill_time = time.monotonic()
                killed_pids = processes.terminate_worker(victim)
                await asyncio.to_thread(client.rpush, "phase6:events", json.dumps({
                    "kind": "kill", "pids": sorted(killed_pids), "time": time.time()}))
                values.update(killed_worker_pid=victim.pid, killed_run_ids=killed_runs,
                              surviving_slow_run=survivor["id"], stage="real-broker-outage")
                proxy.disconnect()
                dropped_at_outage = proxy.dropped
                assert dropped_at_outage > 0, "No real broker transport was disconnected"

                # Keep one provider blocked beyond its original 90s lease, releasing others.
                async def release_remaining():
                    while True:
                        ids = set(await asyncio.to_thread(client.hkeys, "phase6:entries"))
                        ids -= {survivor["id"], *killed_runs}
                        if ids:
                            await asyncio.to_thread(client.sadd, "phase6:release", *ids)
                        await asyncio.sleep(0.1)

                releaser = asyncio.create_task(release_remaining())
                monitors.append(releaser)
                await asyncio.sleep(10)
                assert proxy.rejected > 0, "Workers/publishers never attempted disconnected transport"
                outage_rows = await asyncio.to_thread(snapshot)
                assert any(r["publish_attempts"] > 1 for r in outage_rows), "No durable publication retry exercised"
                assert all(r["status"] == "running" for r in outage_rows if r["id"] in killed_runs)
                reconnect_time = time.monotonic()
                reconnect_wall_time = time.time()
                proxy.up = True
                values["broker_outage_seconds"] = reconnect_time - kill_time
                values["publication_attempts_during_outage"] = {
                    row["id"]: row["publish_attempts"] for row in outage_rows
                }
                values["broker_connections_dropped"] = dropped_at_outage
                values["broker_connections_rejected"] = proxy.rejected
                values["stage"] = "worker-lease-recovery"

                async def lost_work_failed():
                    current = await asyncio.to_thread(snapshot)
                    return all(r["status"] == "failed" for r in current if r["id"] in killed_runs)

                await until(lost_work_failed, timeout=100, description="default execution lease expiry and scheduler recovery")
                values["worker_kill_to_failed_seconds"] = time.monotonic() - kill_time
                assert values["worker_kill_to_failed_seconds"] < 105
                current = await asyncio.to_thread(snapshot)
                survived = next(r for r in current if r["id"] == survivor["id"])
                assert survived["status"] == "running"
                assert survived["execution_owner"] == survivor["execution_owner"]
                assert survived["heartbeat_at"] > survivor["lease_expires_at"]
                values["slow_provider_seconds"] = time.time() - owners[survivor["id"]]["time"]
                assert values["slow_provider_seconds"] > 90
                for row in current:
                    if row["id"] in killed_runs:
                        assert "submit a new run" in row["exit_reason"]
                await asyncio.to_thread(client.sadd, "phase6:release", survivor["id"])

                async def originals_terminal():
                    current = await asyncio.to_thread(snapshot)
                    return len(current) == 10 and all(r["status"] in {"completed", "failed"} for r in current)

                await until(originals_terminal, timeout=30, description="all original work terminal, no stranded reservations")
                values["broker_restore_to_all_terminal_seconds"] = time.monotonic() - reconnect_time
                final_originals = await asyncio.to_thread(snapshot)
                assert sum(r["status"] == "failed" for r in final_originals) == len(killed_runs)
                lost = next(r for r in final_originals if r["id"] in killed_runs)
                old_deliveries = int(await asyncio.to_thread(client.hget, "phase6:deliveries", lost["id"]))
                await redeliver(lost)

                async def failed_redelivery_seen():
                    return int(await asyncio.to_thread(client.hget, "phase6:deliveries", lost["id"])) > old_deliveries

                await until(failed_redelivery_seen, description="failed workflow redelivery fenced")
                assert int(await asyncio.to_thread(client.hget, "phase6:entries", lost["id"])) == 1
                values["stage"] = "explicit-new-submission"
                # Repeat broker recovery with the survivor's provider no longer gated;
                # this separates transport recovery from non-preemptive pool drainage.
                proxy.disconnect()
                retry_id = await submit(0, "explicit new run after worker loss")
                assert retry_id not in all_run_ids
                all_run_ids.append(retry_id)
                await asyncio.sleep(3)
                retry_row = next(row for row in await asyncio.to_thread(snapshot) if row["id"] == retry_id)
                assert retry_row["status"] == "queued"
                assert not await asyncio.to_thread(client.hexists, "phase6:entries", retry_id)
                retry_restore = time.monotonic()
                proxy.up = True

                async def retry_completed():
                    current = await asyncio.to_thread(snapshot)
                    return any(r["id"] == retry_id and r["status"] == "completed" for r in current)

                await until(retry_completed, timeout=30,
                            description="explicit new submission completes after independent broker recovery")
                values["ungated_broker_restore_to_completed_seconds"] = time.monotonic() - retry_restore
                counts = {key: int(value) for key, value in
                          (await asyncio.to_thread(client.hgetall, "phase6:entries")).items()}
                assert set(counts) == set(all_run_ids)
                assert all(count == 1 for count in counts.values()), counts
                assert all(r["state"] in {"finished", "failed"} for r in await asyncio.to_thread(snapshot))
                values.update(body_entry_counts=counts, duplicate_body_entries=0,
                              explicit_retry_run_id=retry_id, final_occupied_slots=0)
                events = [json.loads(event) for event in
                          await asyncio.to_thread(client.lrange, "phase6:events", 0, -1)]
                executing, max_bodies = {}, 0
                for event in events:
                    if event["kind"] == "enter":
                        assert event["run_id"] not in executing
                        executing[event["run_id"]] = event["pid"]
                    elif event["kind"] == "exit":
                        executing.pop(event["run_id"])
                    elif event["kind"] == "kill":
                        executing = {run_id: pid for run_id, pid in executing.items()
                                     if pid not in event["pids"]}
                    max_bodies = max(max_bodies, len(executing))
                assert not executing
                assert max_bodies == 5
                values["max_observed_simultaneous_workflow_bodies"] = max_bodies
                after_restore = [event["time"] for event in events
                                 if event["kind"] == "enter" and event["time"] > reconnect_wall_time
                                 and event["run_id"] != retry_id]
                assert after_restore, "No pending workflow consumed after real transport recovery"
                values["broker_restore_to_next_body_seconds"] = min(after_restore) - reconnect_wall_time
                values["combined_fault_recovery_bound_seconds"] = 105
                values["combined_fault_caveat"] = (
                    "Celery reconnect/prefetch can defer new bodies while the surviving provider is gated; "
                    "combined recovery includes default execution-lease expiry and provider release. "
                    "The separate ungated broker probe has a 30-second recovery bound."
                )
                assert values["broker_restore_to_next_body_seconds"] < 105
                for stream in streams:
                    stream.cancel()
                await asyncio.gather(*streams, return_exceptions=True)

                async def no_readers():
                    return await reader_count() == [0, 0]

                await until(no_readers, timeout=5, description="all SSE readers reclaimed")
                values["sse_readers_after_cleanup"] = [0, 0]
                for monitor in monitors:
                    assert not monitor.done(), "A monitoring task failed during acceptance"
                assert max(row["slots"] for row in rows_seen) == 5
                assert max(row["running"] for row in rows_seen) == 5
                values.update(max_sampled_slots=5, max_sampled_running=5, samples=len(rows_seen))
                for phase, samples in latencies.items():
                    p95 = sorted(samples)[math.ceil(len(samples) * 0.95) - 1]
                    values.setdefault("unrelated_read_latency", {})[phase] = {
                        "count": len(samples), "p95_seconds": p95, "max_seconds": max(samples)}
                    assert p95 < 2, (phase, p95)
                assert sum(map(len, latencies.values())) >= 100
                values["stage"] = "verified"
        finally:
            try:
                for task in streams + monitors:
                    task.cancel()
                outcomes = await asyncio.wait_for(asyncio.gather(
                    *(streams + monitors), return_exceptions=True), 15)
                values["background_errors"] = [repr(error) for error in outcomes
                                               if isinstance(error, Exception)]
                values["elapsed_seconds"] = time.monotonic() - started
                events = await asyncio.to_thread(client.lrange, "phase6:events", 0, -1)
                Path("/results/phase6-events.json").write_text("[" + ",".join(events) + "]", encoding="utf-8")
                Path("/results/phase6-samples.json").write_text(json.dumps(rows_seen), encoding="utf-8")
                Path("/results/phase6-metrics.json").write_text(json.dumps(values, indent=2), encoding="utf-8")
            finally:
                try:
                    await processes.close()
                finally:
                    await proxy.close()
                    if channel:
                        channel.close()
                    metrics(**values)

    asyncio.run(acceptance())
