"""
Pytest configuration and shared fixtures for LIZARD tests.
"""
import os
import pytest
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Callable
from uuid import uuid4
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

# Load environment variables from the project .env so tests respect Docker/CLI config
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

# Set test environment
os.environ["ENVIRONMENT"] = "testing"

# Use temporary directory for test data
test_data_dir = tempfile.mkdtemp(prefix="lizard_test_")
os.environ["LIZARD_DATA_ROOT"] = test_data_dir

test_db_password = os.getenv('POSTGRES_PASSWORD')
test_redis_password = os.getenv('REDIS_PASSWORD')
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    f"postgresql://lizard_user:{test_db_password}@postgres:5432/lizard_test"
)
os.environ["REDIS_URL"] = os.getenv(
    "TEST_REDIS_URL",
    f"redis://:{test_redis_password}@redis:6379/1"  # Use DB 1 for tests
)
os.environ["SECRET_KEY"] = "5e80dafeb9d8937ca8de309c50a04d9361764a60a017f39ec05bc0ccde680e2e"

# Debug output for environment
print("\n" + "="*70)
print("TEST ENVIRONMENT CONFIGURATION")
print("="*70)
print(f"DATABASE_URL: {os.environ['DATABASE_URL']}")
print(f"REDIS_URL: {os.environ['REDIS_URL']}")
print(f"ENVIRONMENT: {os.environ['ENVIRONMENT']}")
print("="*70 + "\n")

from server.database import Base, get_db
from server.app import app
from server.models.user import User
from server.models.session import Session as SessionModel
from server.models.run import Run
from server.models.conversation import Conversation
from server.schemas.conversation import ConversationState
from server.auth.password import hash_password, verify_password
from server.services.cache_service import cache_service
from server.models.password_reset import PasswordResetToken


# Database fixtures
@pytest.fixture(scope="session")
def test_engine():
    """Create a test database engine."""
    engine = create_engine(
        os.environ["DATABASE_URL"],
        pool_pre_ping=True,
        echo=False  # Set to True for SQL debugging
    )
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    
    yield engine
    
    # Drop all tables after tests
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(test_engine) -> Generator[Session, None, None]:
    """Create a new database session for each test."""
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine
    )
    
    session = TestingSessionLocal()
    
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="function")
def override_get_db(db_session):
    """Override the get_db dependency for testing."""
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass
    
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()


# API Client fixtures
@pytest.fixture(scope="function")
def client(override_get_db) -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


# Redis fixtures
@pytest.fixture(scope="function", autouse=True)
def clear_redis_cache():
    """Clear Redis cache before each test."""
    if cache_service.is_connected():
        cache_service.clear_all()
    yield
    if cache_service.is_connected():
        cache_service.clear_all()


# User fixtures
@pytest.fixture
def test_user_data():
    """Test user registration data."""
    return {
        "email": "test@example.com",
        "username": "testuser",
        "password": "Tr0ngP@ssw0rd!"
    }


@pytest.fixture
def test_user(db_session, test_user_data) -> User:
    """Create a test user in the database."""
    existing = db_session.query(User).filter_by(email=test_user_data["email"]).one_or_none()
    if existing:
        # Ensure fixture password and flags are up to date
        if not verify_password(test_user_data["password"], existing.password_hash or ""):
            existing.password_hash = hash_password(test_user_data["password"])
        existing.is_active = True
        existing.is_verified = True
        db_session.commit()
        db_session.refresh(existing)
        return existing
    user = User(
        email=test_user_data["email"],
        username=test_user_data["username"],
        password_hash=hash_password(test_user_data["password"]),
        is_active=True,
        is_verified=True
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_user_token(client, test_user, test_user_data) -> str:
    """Get a valid JWT token for the test user."""
    response = client.post(
        "/auth/login",
        json={
            "email": test_user_data["email"],
            "password": test_user_data["password"]
        }
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.fixture
def auth_headers(test_user_token) -> dict:
    """Get authorization headers with JWT token."""
    return {"Authorization": f"Bearer {test_user_token}"}


# Additional user fixtures
@pytest.fixture
def another_user(db_session) -> User:
    """Create another test user for authorization tests."""
    existing = db_session.query(User).filter_by(email="other@example.com").one_or_none()
    if existing:
        return existing
    user = User(
        email="other@example.com",
        username="otheruser",
        password_hash=hash_password("0therUs3r!@#"),
        is_active=True,
        is_verified=True
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def inactive_user(db_session) -> User:
    """Create an inactive test user."""
    existing = db_session.query(User).filter_by(email="inactive@example.com").one_or_none()
    if existing:
        return existing
    user = User(
        email="inactive@example.com",
        username="inactiveuser",
        password_hash=hash_password("In@ctiv3Us3r!"),
        is_active=False,
        is_verified=True
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# Session / conversation / run factories
@pytest.fixture
def make_session(db_session) -> Callable[[User], SessionModel]:
    """Factory to create sessions that match the current schema."""

    def _make_session(user: User, **overrides) -> SessionModel:
        now = datetime.now(timezone.utc)
        session = SessionModel(
            user_id=user.id,
            token=overrides.get("token", f"test-session-{uuid4()}"),
            ip_address=overrides.get("ip_address"),
            user_agent=overrides.get("user_agent", "pytest"),
            created_at=overrides.get("created_at", now),
            last_activity=overrides.get("last_activity", now),
            expires_at=overrides.get("expires_at", now + timedelta(hours=24)),
            is_active=overrides.get("is_active", True),
            extra_metadata=overrides.get("extra_metadata", {}),
        )
        db_session.add(session)
        db_session.flush()
        return session

    return _make_session


@pytest.fixture
def make_conversation(db_session, make_session) -> Callable[[User], Conversation]:
    """Factory to create conversations for tests with up-to-date fields."""

    def _make_conversation(
        user: User,
        *,
        state: str = ConversationState.GREETING.value,
        context: dict | None = None,
        session: SessionModel | None = None,
    ) -> Conversation:
        session_obj = session or make_session(user)
        conversation = Conversation(
            user_id=user.id,
            session_id=session_obj.id,
            status=state,
            context=context or {},
        )
        db_session.add(conversation)
        db_session.commit()
        db_session.refresh(conversation)
        return conversation

    return _make_conversation


@pytest.fixture
def make_run(db_session, make_session) -> Callable[[User], Run]:
    """Factory to create runs for tests with the latest schema."""

    def _make_run(
        user: User,
        *,
        run_id: str | None = None,
        session: SessionModel | None = None,
        prompt: str = "Test optimization prompt",
        status: str = "pending",
        starting_molecules: list[str] | None = None,
        note: str | None = None,
        extra_metadata: dict | None = None,
    ) -> Run:
        session_obj = session or make_session(user)
        run = Run(
            id=run_id or f"test-run-{uuid4().hex[:8]}",
            user_id=user.id,
            session_id=session_obj.id,
            prompt=prompt,
            status=status,
            starting_molecules=starting_molecules or ["CC(=O)Oc1ccccc1C(=O)O"],
            note=note,
            extra_metadata=extra_metadata or {"max_iterations": 10, "batch_size": 5},
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        return run

    return _make_run


# Conversation fixtures
@pytest.fixture
def test_conversation(test_user, make_conversation) -> Conversation:
    """Create a default conversation for tests."""
    return make_conversation(test_user)


# Run fixtures
@pytest.fixture
def test_run(test_user, make_run) -> Run:
    """Create a default run for tests."""
    return make_run(test_user)


# Mock data fixtures
@pytest.fixture
def sample_smiles():
    """Sample SMILES strings for testing."""
    return [
        "CC(=O)Oc1ccccc1C(=O)O",  # Aspirin
        "CC(C)Cc1ccc(cc1)C(C)C(=O)O",  # Ibuprofen
        "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",  # Caffeine
    ]


@pytest.fixture
def sample_conversation_context():
    """Sample conversation context for testing."""
    return {
        "starting_molecule": "CC(=O)Oc1ccccc1C(=O)O",
        "molecule_name": "aspirin",
        "targets": [
            {
                "property": "QED",
                "mode": "maximize",
                "weight": 1.0
            },
            {
                "property": "logP",
                "mode": "match",
                "target_value": 2.5,
                "weight": 0.5
            }
        ],
        "iterations": 10,
        "batch_size": 5
    }


# Cleanup helpers
@pytest.fixture(scope="function", autouse=True)
def cleanup_after_test(db_session):
    """Clean up database after each test."""
    yield
    # Rollback any uncommitted changes
    db_session.rollback()
    
    # Clean up all tables to prevent unique constraint violations
    try:
        db_session.query(Run).delete()
        db_session.query(SessionModel).delete()
        db_session.query(Conversation).delete()
        db_session.query(PasswordResetToken).delete()
        # db_session.query(Run).delete()
        db_session.query(User).delete()
        db_session.commit()
    except Exception:
        db_session.rollback()
