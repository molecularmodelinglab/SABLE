"""Disposable child interpreter bootstrap; only external execution is controlled."""
import importlib
import json
import os
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace

import redis


def control():
    return redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True,
                               socket_connect_timeout=2, socket_timeout=2)


def unavailable(*args, **kwargs):
    raise AssertionError("External provider access is forbidden in Phase 6")


class DisabledExtractor:
    def __init__(self, **kwargs):
        pass

    def __getattr__(self, name):
        return unavailable


class ControlledWorkflowRunner:
    def __init__(self, checkpoint_dir):
        self.checkpoint_dir = Path(checkpoint_dir)

    def run(self, *, extensions, event_callback, **kwargs):
        run_id = extensions["execution"]["run_id"]
        client = control()
        event = {"kind": "enter", "run_id": run_id, "pid": os.getpid(),
                 "worker_pid": os.getppid(), "time": time.time()}
        client.hincrby("phase6:entries", run_id, 1)
        client.hset("phase6:owners", run_id, json.dumps(event))
        client.rpush("phase6:events", json.dumps(event))
        event_callback({"message": "Controlled provider entered", "level": "INFO"})
        deadline = time.monotonic() + 210
        while not client.sismember("phase6:release", run_id):
            if time.monotonic() >= deadline:
                raise TimeoutError("Phase 6 provider gate exceeded its bounded wait")
            time.sleep(0.1)
        client.rpush("phase6:events", json.dumps({**event, "kind": "exit", "time": time.time()}))
        return SimpleNamespace(starting_molecules=[], parsed_arguments={}, targets=[],
                               best_molecules=[], current_iteration=0, experimental_results=[],
                               bo_rounds=[], summary="Controlled provider only", status="completed",
                               exit_reason="phase6-controlled-provider")

    def export_results(self, state, path):
        Path(path).write_text(json.dumps({"controlled_provider": True}), encoding="utf-8")


def bootstrap():
    if os.getenv("SABLE_CONCURRENCY_ISOLATED") != "1" or Path("/suite/.env").exists():
        raise RuntimeError("Phase 6 requires the isolated concurrency container")
    for name, exports in {
        "run_workflow": {"WorkflowRunner": ControlledWorkflowRunner},
        "nodes.extract_arguments_hybrid": {"HybridArgumentExtractor": DisabledExtractor},
        "utils.llm_factory": {"get_llm_client": lambda: None},
        "utils.hpc_client": {"HPCClient": unavailable, "SSHConfig": unavailable},
        "utils.job_managers": {"BoltzJobManager": unavailable, "JobState": unavailable},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(exports)
        sys.modules[name] = module
    for path in Path("/suite/server/models").glob("*.py"):
        if path.stem != "__init__":
            importlib.import_module(f"server.models.{path.stem}")


def main():
    bootstrap()
    if sys.argv[1] == "api":
        import asyncio
        import threading
        from fastapi import FastAPI
        import uvicorn
        from server.routers import runs
        from server.services.scheduler_lifecycle import scheduler_lifespan

        app = FastAPI(lifespan=scheduler_lifespan)
        app.include_router(runs.router)

        @app.get("/phase6/health")
        async def health():
            return {"pid": os.getpid(),
                    "scheduler": any(t.name == "run-reconciler" and t.is_alive()
                                     for t in threading.enumerate()),
                    "event_readers": sum("RunEventHub._receive" in repr(t.get_coro())
                                         for t in asyncio.all_tasks())}

        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]), log_level="info",
                    timeout_graceful_shutdown=3)
    else:
        from celery.signals import task_prerun, worker_ready
        from server.tasks.celery_app import celery_app

        @worker_ready.connect(weak=False)
        def ready(sender, **kwargs):
            control().hset("phase6:workers", str(os.getpid()), sender.hostname)

        @task_prerun.connect(weak=False)
        def delivery(task_id, task, args, **kwargs):
            if task.name == "server.tasks.workflow.run_workflow":
                control().hincrby("phase6:deliveries", args[0], 1)

        celery_app.worker_main(["worker", "--pool=prefork", "--concurrency=3",
                                "--hostname=" + sys.argv[2], "--loglevel=INFO",
                                "--pidfile=", "--without-gossip", "--without-mingle"])


if __name__ == "__main__":
    main()
