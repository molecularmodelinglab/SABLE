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
