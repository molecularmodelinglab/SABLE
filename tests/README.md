# SABLE Tests

Comprehensive test suite for the SABLE application with 96 test cases covering authentication, services, API endpoints, and complete user workflows.

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up Test Database

```bash
# Option 1: Using createdb
createdb sable_test

# Option 2: Using Docker
docker-compose exec postgres psql -U sable_user -c "CREATE DATABASE sable_test;"

# Option 3: Using psql directly
psql -U postgres -c "CREATE DATABASE sable_test;"
```

### 3. Configure Environment

Create `.env.test` (optional - defaults are provided):

```bash
TEST_DATABASE_URL=postgresql://sable_user:sable_password@localhost:5432/sable_test
TEST_REDIS_URL=redis://localhost:6379/1
SECRET_KEY=test-secret-key-for-testing-only-min-32-chars
ENVIRONMENT=testing
```

### 4. Run Tests

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=server --cov-report=html

# View coverage report
open htmlcov/index.html
```

## Isolated Concurrency Acceptance Tests (Phase 1)

The opt-in [concurrency suite](concurrency/) asserts the behavior required by the
[remediation plan](../CONCURRENCY_PLAN.md). It is separate from the ordinary test
suite and does not load the parent fixtures, application `.env`, or production services.

From the repository root, with Docker Desktop running Linux containers:

```powershell
.\tests\concurrency\Run.ps1

# Select one area; the same isolation and cleanup still apply.
.\tests\concurrency\Run.ps1 -PytestArgs @('-k', 'scheduler')
```

The runner builds a small API-only image, creates a uniquely named Compose project,
and removes its containers, network, and volumes in `finally`, including when tests
fail. Docker must be able to fetch images and Python dependencies during setup;
the test services themselves use an internal network with no published ports.
PostgreSQL data is temporary, and Redis persistence is disabled. Server source,
migrations, and this test directory are mounted read-only; reports have a separate
writable mount. No credentials, application data, or production configuration are mounted.

### Structure and boundaries

- [conftest.py](concurrency/conftest.py): disposable database fixtures, two user
  identities, real FastAPI routers, a local HTTP server, and per-test measurements.
- [test_api.py](concurrency/test_api.py): legacy slow-chat regressions and shared
  ownership checks for progress/artifacts. The current UI does not use conversations.
- [test_submission.py](concurrency/test_submission.py): current single-prompt flow,
  using real JWT/session authentication; slow storage, database, cache, broker, and
  authentication operations; queued acceptance and capacity reservations, persisted
  prompts and notes, cross-user ownership, and rejected requests that create/dispatch no run.
- [test_events.py](concurrency/test_events.py): 44 idle stream iterators, request
  thread capacity, fresh-process event delivery, per-run/logical-DB isolation,
  live-only reconnects, heartbeats, bounded buffers, Redis failures, real HTTP
  disconnect/ownership, DB release, send timeouts, and cancellation cleanup.
- [test_scheduler.py](concurrency/test_scheduler.py): real PostgreSQL admission and
  queue races across threads/processes, reserved capacity, and shared global limits.
- [test_worker.py](concurrency/test_worker.py) and
  [test_recovery.py](concurrency/test_recovery.py): duplicate/stale deliveries,
  heartbeats and fenced completion, outbox recovery, startup failures, API lifespan
  reconciliation, actual Celery-to-Redis publication, migration parity, and stale caches.
- [test_storage.py](concurrency/test_storage.py): unrelated-run responsiveness,
  same-run thread/process deduplication, atomic publication, interrupted downloads,
  killed downloader recovery, shared-root cache validation, and sync/delete coordination.
- [test_fairness.py](concurrency/test_fairness.py): least-admitted-user ordering,
  work-conserving capacity, batch recalculation, administrator parity, and authorized
  queue explanations on real HTTP submission/detail/list endpoints.

Workflow/broker dispatch, blob downloads, heavyweight provider imports, and legacy
conversation processing are simulated. Submission responsiveness tests additionally
pause one synchronous submission/authentication call, then invoke its real
implementation (except broker dispatch). The original modules inject user identity;
submission tests instead issue test JWTs backed by real database sessions and run
the actual authentication dependencies. No login endpoint is used. Real ownership
checks and per-request database sessions remain active. The SSE testing bypass is disabled.
Scheduler processes use separate database connections. Event delivery is tested
with a fresh producer interpreter, not an inherited subscriber registry.

These are controlled regression tests, not production load tests or a full Celery
worker deployment. Phase 3 uses live-only events without replay. Worker recovery is
covered by Phase 4 below; storage isolation and fairness are covered by Phase 5. Streaming tests use asynchronous Redis
subscriptions and real stream iterators; ownership checks also exercise HTTP routes.

### Results and expected baseline

Each invocation writes `junit.xml` and `metrics.jsonl` under
`tests/concurrency/results/<unique-project>/`, printed by the runner and ignored
by Git. Measurements include request latency, occupied thread slots, queue age,
dispatch counts, and duplicate dispatches. Queue age uses the persisted creation
time of fixture runs placed directly into the queue. One-second observation windows
check whether unrelated requests finish **before** a controlled slow operation is
released; they are regression-test bounds, not agreed production latency targets.

The Phase 1 baseline was verified twice on 2026-10-12: **14 tests, 10 failures,
4 passes, zero errors**. Failures assert desired behavior and deliberately remain
ordinary failures, not `xfail` or inverted bug assertions. The passing controls
cover ownership, same-process subscription isolation/cleanup, the existing global
five-run policy, and same-run hydration serialization. Exit code 1 is therefore
expected until later remediation phases fix the failures; setup errors are not an
acceptable baseline. The root pytest configuration excludes this opt-in directory.

### Phase 2: single-prompt submission

The current UI posts a single prompt directly to `/runs`; workflow execution stays
in Celery through the existing scheduler. No separate chat queue was added. The
19 new submission tests first produced 10 expected responsiveness failures and 9
passing controls. After moving submission and its blocking authentication dependency
off the event loop, **all 19 pass**, as does the original ownership control.

Run this focused, passing acceptance set from the repository root:

```powershell
.\tests\concurrency\Run.ps1 -PytestArgs @('-k', 'test_submission or two_users_cannot')
```

Legacy slow-chat regressions remain unchanged and excluded from this selection;
conversation endpoints have not been removed or repaired. SSE is addressed in
Phase 3 below; scheduler and hydration failures remain separate. These two-user
checks do not establish behavior at request-pool saturation or fix scheduling races.

### Phase 3: live-only progress streaming

**21 streaming tests pass**, plus **3 adjacent ownership and running/queued controls**
in a separate focused run. The 44-idle-stream test reports zero borrowed AnyIO
thread slots, a responsive unrelated request, and zero Redis subscriptions after
cleanup. An independent interpreter publishes through Redis to the API subscriber.

```powershell
.\tests\concurrency\Run.ps1 -PytestArgs @('-k', 'test_events')
```

API and workflow processes must use the same `REDIS_URL`. Redis channels include
the logical database number as well as the run ID. No replay or `Last-Event-ID`
processing is provided; existing status/log polling remains unchanged. The stream
sends 15-second idle heartbeat comments and a 3-second retry hint. Each client has
a 256-event buffer and 15-second send timeout. Overflow or Redis loss ends the
stream for browser reconnection, while NDJSON logging survives publish failures.
Database connections are released before streaming; async Redis readers/connections
are released on stream termination, including cancellation and stalled sends.

Redis outages and stalled network sends are controlled boundary failures in these
tests, not production failure-injection/load tests. Neither scheduler admission nor
workflow execution semantics change in Phase 3.

### Phase 4: durable scheduling and worker ownership

**70 unique Phase 4/submission tests and 21 streaming regressions passed** across
an initial selection and a focused follow-up. The initial selection also included
the two known legacy slow-chat responsiveness failures; both remain unchanged.
There were no setup errors. Both disposable Compose projects were removed.

```powershell
.\tests\concurrency\Run.ps1 -PytestArgs @('-k', 'test_scheduler or test_submission or test_worker or test_recovery')
```

Coverage includes real multi-process admission, duplicate worker claims, broker
retry/crash recovery, stale-owner fencing, quiet heartbeats, heartbeat-start failure,
expired leases after database lock waits, delayed renewal beyond the local deadline,
setup failures, stale cache fills, real Celery/Redis token delivery, and isolated
migration upgrade/downgrade/schema parity. Workflow execution/provider calls remain
controlled substitutes rather than a full production Celery worker deployment.

Submissions stay publicly `queued` while waiting for capacity or broker delivery.
Only a worker holding the current dispatch token can mark a run `running`.
Reservations, dispatched work, and running leases all consume the shared database
capacity; the default remains five across all API and worker processes. Phase 5
adds fair ordering without changing this ceiling.

A transactional outbox records delivery intent before publishing. Publication and
workflow claims are separate: uncertain broker acknowledgements may cause duplicate
messages, but only one worker can claim a reservation. Expired unclaimed deliveries
can be retried with a new token. Expired **running** leases instead mark the run
`failed`; an operator/user must explicitly submit a new run after checking any
external provider jobs. A worker that loses ownership cannot update the run's
molecules or final status. Independent heartbeats protect quiet, long-running work.
API lifespan reconciliation runs outside the request event loop and the Celery
workflow pool, so recovery does not require a new submission or a free workflow worker.

Default timings are 5-second reconciliation, 300-second unclaimed reservations,
30-second publication claims, 15-second heartbeats, and 90-second worker leases.
Failed publication backs off from 2 seconds to a maximum of 60 seconds. Broker
connection/socket operations are bounded at 5 seconds with task publication retries
disabled; durable retries belong to the outbox. Database connection/pool acquisition
and lock waits are bounded, with a 10-second statement timeout. These are operation
bounds, not a hard total deadline for a recovery pass or guarantees against a network
blackhole. Local monotonic lease deadlines prevent a delayed heartbeat from granting
indefinite ownership.

Mutable run snapshots and lists containing them are not cached; legacy nonterminal
cache entries are ignored. Terminal snapshots remain cacheable. This prevents a
late queued/running cache fill from hiding a fenced terminal transition, at the cost
of database reads while runs are active. It does not redesign list membership caching.

**Rollout:** Stop new submissions and drain old workflows before upgrading. Stop
old API/worker processes, apply the new Alembic migration using the normal deployment
procedure, and deploy the matching API and worker code together. Do not mix old and
new schedulers. Old one-argument Celery workflow messages are rejected; check/purge
obsolete deliveries only after draining and reconciling their runs. Legacy `running`
rows without a lease still consume capacity and are never automatically expired:
verify their external jobs and resolve them explicitly before resuming submissions.
The database stores the shared capacity; use `set_max_concurrent` for an intentional
change rather than expecting different API-process environment values to override it.

**Limits:** A lease cannot cancel an already-running provider call or forcibly stop
Python workflow code. Token fencing protects database run state, not exactly-once
external effects or atomic artifact writes. Investigate provider jobs and artifacts
before explicitly retrying an abandoned run. These isolated regressions do not replace
the later multi-worker deployment, worker-kill, and production load exercises.

### Phase 5: storage isolation and fair admission

**127 tests passed:** 17 storage tests, 19 fairness/queue tests, and 91 adjacent
scheduler/submission/worker/recovery/streaming tests. Reports are retained under
[results](concurrency/results/) in `sable-concurrency-9c450e8ca970`,
`sable-concurrency-44348243d886`, and `sable-concurrency-535945f6f056`; all three
isolated projects were removed. Native Windows checks additionally passed same-run
process deduplication, unrelated-run responsiveness, and killed-downloader recovery
without exposing partial artifacts (`storage-windows-c07f017637c3/report.json`).

```powershell
.\tests\concurrency\Run.ps1 -PytestArgs @('-k', 'test_storage or test_fairness')
.\tests\concurrency\Run.ps1 -PytestArgs @('-k', 'test_scheduler or test_submission or test_worker or test_recovery or test_events')
```

**Fairness policy:** For each available slot, choose the eligible prompt whose user
has the fewest admitted runs, then the oldest creation time, then run ID. Count
reserved, dispatched, running, and legacy running runs; increment the chosen user's
count before assigning another slot. Broker retry backoff remains in force. There
is no per-user hard cap, preemption, administrator priority, or administrator
capacity bypass. One user may occupy all otherwise idle slots. A competing user is
preferred when capacity next becomes available, not by interrupting existing work.
The shared persisted capacity remains authoritative. No new migration is required.

**Queue visibility:** Public status remains `queued` until a worker claims the run.
Authorized create/detail/list responses, including admin views, add optional
`queue_reason` and `queue_retry_at`. Queue metadata is read-only and batched per
response; active results are not cached. The UI displays explanations using its
existing polling, without promising queue positions or exposing dispatch tokens,
owners, or other users' data.

| Reason | Meaning |
|---|---|
| `waiting_for_admission` | Waiting for available capacity under the fair ordering policy. |
| `dispatch_retry` | Broker delivery failed; `queue_retry_at` is the earliest retry eligibility, not a promised start time. |
| `dispatching` | Capacity reserved; delivery is being arranged. |
| `waiting_for_worker` | Delivery recorded; waiting for a worker to claim the run. |

Running/terminal runs have null queue fields. A concurrent transition can briefly
leave a queued snapshot without a reason; subsequent polling refreshes it. The UI
build and browser smoke checks passed all four explanations, valid/invalid retry
dates, status-only compatibility, and a 375px viewport without horizontal overflow.
Default `tsc --noEmit` remains blocked by a pre-existing `replaceAll`/ES2020 library
mismatch; `tsc --noEmit --lib ES2021,DOM` passes. Build warnings remain for existing
3dmol `eval` use and chunk size. See `results/phase5-queue-ui.json`.

**Storage coordination:** [Storage](../server/storage.py) and the small
[lock helper](../server/storage_lock.py) share one canonical per-run lock across
hydration, synchronization, and deletion. Unrelated runs do not share it. Download
staging lives outside artifact directories; same-filesystem hard links publish
complete files atomically without overwriting existing local files. Failures do
not record completed hydration. A later caller cleans staging left by a dead process.

For a canonical `data/runs/<id>` directory, coordination state lives under
`data/.sable-storage/<hash>/`: a permanent `lock`, a `hydrated` manifest, and transient
`staging`. **Do not remove lock files while processes may use this storage root.**
All cooperating processes must resolve the same canonical paths and share reliable
filesystem advisory locks and hard-link support (tested on Linux and Windows/NTFS).
Unsupported publication fails closed; arbitrary NFS/SMB, power-loss durability, and
forking while a lock is held are not validated. A live stalled holder can still block
same-run callers until it releases the lock.

Completion manifests check directory identity, a SHA-256 source fingerprint, and
recorded file existence. They contain no raw Azure credentials. Missing files or
invalid manifests cause another hydration attempt; existing local files remain
untouched. Newly added remote blobs and changed local file contents do not themselves
invalidate the cache. Sync/delete invalidate completion before mutation. Atomicity
is **per downloaded file**, not an entire directory snapshot; raw workflow writes
and reads after returning a local path are not protected by the storage lock.

Full multi-worker/load/worker-termination rollout exercises remain Phase 6. The two
legacy slow-conversation regressions remain outside the single-prompt acceptance set.

## Test Types

### Unit Tests (53 tests)
Fast tests for individual functions and classes.

```bash
pytest -m unit
```

**Coverage:**
- Password hashing and validation
- JWT token management
- User service operations
- Redis caching

### Integration Tests (36 tests)
Tests for API endpoints with database.

```bash
pytest -m integration
```

**Coverage:**
- Authentication API (register, login, logout)
- Conversation API (start, message, confirm)
- Runs API (create, list, get, delete)

### End-to-End Tests (7 scenarios)
Complete user workflow tests.

```bash
pytest -m e2e
```

**Coverage:**
- Full user journey: register → login → create run
- Conversational workflow
- Multi-user isolation
- Session management


## Running Specific Tests

```bash
# Single file
pytest tests/unit/test_auth_password.py

# Single test class
pytest tests/unit/test_auth_password.py::TestPasswordHashing

# Single test function
pytest tests/unit/test_auth_password.py::TestPasswordHashing::test_hash_password

# Skip slow tests
pytest -m "not slow"

# Verbose output
pytest -v

# Show print statements
pytest -s
```

## Coverage

### Generate Coverage Report

```bash
# Terminal report
pytest --cov=server --cov-report=term-missing

# HTML report
pytest --cov=server --cov-report=html
open htmlcov/index.html

# XML report (for CI)
pytest --cov=server --cov-report=xml
```

## Fixtures

Common fixtures available in all tests (defined in `conftest.py`):

### Database
- `db_session` - Clean database session (auto-rollback)
- `test_engine` - SQLAlchemy engine

### API
- `client` - FastAPI TestClient
- `override_get_db` - Database dependency override

### Users
- `test_user` - Pre-created test user
- `test_user_token` - Valid JWT token
- `auth_headers` - Authorization headers
- `test_user_data` - User registration data
- `another_user` - Second test user
- `inactive_user` - Inactive user

### Data
- `test_conversation` - Sample conversation
- `test_run` - Sample run
- `sample_smiles` - SMILES strings
- `sample_conversation_context` - Conversation context

## Example Test

```python
import pytest

@pytest.mark.unit
def test_user_creation(db_session):
    """Test user creation with all fixtures."""
    from server.services.user_service import user_service
    
    user, error = user_service.create_user(
        db_session,
        "test@example.com",
        "testuser",
        "SecurePassword123!"
    )
    
    assert error is None
    assert user is not None
    assert user.email == "test@example.com"
```

## Troubleshooting

### Database Connection Error

```bash
# Check if PostgreSQL is running
docker-compose ps postgres

# Check if database exists
psql -U postgres -l | grep sable_test

# Recreate database
dropdb sable_test && createdb sable_test
```

### Redis Connection Error

```bash
# Check if Redis is running
redis-cli ping

# Start Redis with docker
docker-compose up redis
```

### Import Errors

```bash
# Make sure you're in project root
cd /home/kelvin/SABLE

# Set PYTHONPATH
export PYTHONPATH=/home/kelvin/SABLE:$PYTHONPATH

# Or install in development mode
pip install -e .
```

## Continuous Integration

Ready for GitHub Actions:

```yaml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: sable_user
          POSTGRES_PASSWORD: sable_password
          POSTGRES_DB: sable_test
      redis:
        image: redis:7
    
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.9'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: pytest --cov=server --cov-report=xml
```
