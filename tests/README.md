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
PostgreSQL data is temporary, and Redis persistence is disabled. Only the server
source and this test directory are mounted read-only; reports have a separate
writable mount. No credentials, application data, or production configuration are mounted.

### Structure and boundaries

- [conftest.py](concurrency/conftest.py): disposable database fixtures, two user
  identities, real FastAPI routers, a local HTTP server, and per-test measurements.
- [test_api.py](concurrency/test_api.py): another user's run requests during slow
  chat, including run submission and ownership checks for progress/artifacts.
- [test_events.py](concurrency/test_events.py): 44 idle stream iterators, request
  thread capacity, fresh-process event delivery, and subscription isolation/cleanup.
- [test_scheduler.py](concurrency/test_scheduler.py): real PostgreSQL admission and
  queue races across threads/processes, failed dispatch, and the existing global limit.
- [test_storage.py](concurrency/test_storage.py): two-user artifact requests during
  slow Azure hydration, plus protection for concurrent readers of the same run.

Only external execution boundaries are simulated: slow conversation processing,
workflow/broker dispatch, blob downloads, and heavyweight provider imports. User
identity is injected rather than obtained through login; real run ownership checks
and per-request database sessions remain active. The SSE testing bypass is disabled.
Scheduler processes use separate database connections. Event delivery is tested
with a fresh producer interpreter, not an inherited subscriber registry.

These are controlled regression tests, not production load tests or a full Celery
worker deployment. Replay policy, fairness changes, and worker recovery belong to
later phases. Event callbacks/stream iterators exercise the current interfaces;
update those test adapters if a remediation intentionally changes their API.

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
