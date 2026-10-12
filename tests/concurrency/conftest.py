"""Opt-in fixtures; never load the parent suite's dotenv or live-service fixtures."""
import importlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

# Refuse destructive setup unless using the dedicated container endpoints.
if (
    os.getenv("SABLE_CONCURRENCY_ISOLATED") != "1"
    or os.getenv("DATABASE_URL") != "postgresql://concurrency:disposable-test-only@postgres:5432/sable_concurrency"
    or os.getenv("REDIS_URL") != "redis://redis:6379/0"
    or Path("/suite/.env").exists()
):
    raise pytest.UsageError("Run concurrency tests with their isolated compose.yml, not the ordinary suite")


def unavailable(*args, **kwargs):
    raise AssertionError("Unexpected external workflow/provider execution")


class DisabledExtractor:
    def __init__(self, **kwargs):
        pass

    def __getattr__(self, name):
        return unavailable


# Replace only heavy external execution boundaries, not application modules.
for name, exports in {
    "run_workflow": {"WorkflowRunner": unavailable},
    "nodes.extract_arguments_hybrid": {"HybridArgumentExtractor": DisabledExtractor},
    "utils.llm_factory": {"get_llm_client": lambda: None},
}.items():
    module = ModuleType(name)
    module.__dict__.update(exports)
    sys.modules[name] = module

from server.database import Base, SessionLocal, engine

for path in (Path("/suite/server/models")).glob("*.py"):
    if path.stem != "__init__":
        importlib.import_module(f"server.models.{path.stem}")
Base.metadata.create_all(engine)

from server.models.user import User
from server.models.session import Session
from server.models.run import Run
from server.services.cache_service import cache_service


def clear_state():
    with engine.begin() as connection:
        names = ", ".join('"' + table.name + '"' for table in Base.metadata.sorted_tables)
        connection.exec_driver_sql(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    assert cache_service.is_connected(), "Isolated Redis is required, not an optional fake"
    cache_service.redis.flushdb()


@pytest.fixture(autouse=True)
def clean_state():
    clear_state()
    yield
    clear_state()


@pytest.fixture
def two_users():
    with SessionLocal() as db:
        users = tuple(User(id=uuid4(), email=f"{name}@example.test", username=name,
                           is_active=True, is_verified=True, extra_metadata={
                               "boltz_self_hosted_access": {"status": "approved"},
                               "boltz_settings": {"provider": "self_hosted"},
                           })
                      for name in ("alice", "bob"))
        db.add_all(users)
        db.commit()
        for user in users:
            db.refresh(user)
            db.expunge(user)
        return users


@pytest.fixture
def make_run():
    def create(user, status="pending"):
        with SessionLocal() as db:
            session = Session(id=uuid4(), user_id=user.id, token=str(uuid4()),
                              expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
            db.add(session)
            db.flush()
            run = Run(id=f"run_{uuid4().hex}", user_id=user.id, session_id=session.id,
                      status=status, prompt="concurrency fixture", starting_molecules=[],
                      extra_metadata={})
            db.add(run)
            db.commit()
            db.refresh(run)
            db.expunge(run)
            return run
    return create


def pytest_sessionstart(session):
    path = Path("/results/metrics.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item.concurrency_outcome = report.outcome


@pytest.fixture
def metrics(request):
    values = {}
    yield lambda **measurements: values.update(measurements)
    path = Path("/results/metrics.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"test": request.node.nodeid,
                                 "outcome": getattr(request.node, "concurrency_outcome", "setup-error"),
                                 **values}) + "\n")
    print("MEASUREMENTS " + json.dumps(values, sort_keys=True))


@pytest.fixture
def api(two_users):
    from fastapi import FastAPI, Header, HTTPException
    from server.auth.dependencies import get_current_user, get_current_active_user
    from server.routers import conversations, runs

    app = FastAPI()
    app.include_router(conversations.router)
    app.include_router(runs.router)
    users = {str(user.id): user for user in two_users}

    async def identity(x_test_user: str = Header()):
        if x_test_user not in users:
            raise HTTPException(401)
        return users[x_test_user]

    # Distinct identities still exercise real run ownership checks and real DB sessions.
    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[get_current_active_user] = identity
    return app


@pytest.fixture
def live_api(api):
    import socket
    import threading
    import time
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(api, log_level="error", lifespan="off",
                                           timeout_graceful_shutdown=2))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Test HTTP server did not start"
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive(), "Test HTTP server did not shut down"
