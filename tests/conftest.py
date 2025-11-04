"""
Pytest configuration and shared fixtures for LIZARD tests.
"""
import os
import pytest
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

# Set test environment
os.environ["ENVIRONMENT"] = "testing"
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://lizard_user:lizard_password@localhost:5432/lizard_test"
)
os.environ["REDIS_URL"] = os.getenv(
    "TEST_REDIS_URL",
    "redis://localhost:6379/1"  # Use DB 1 for tests
)
os.environ["SECRET_KEY"] = "5e80dafeb9d8937ca8de309c50a04d9361764a60a017f39ec05bc0ccde680e2e"

from server.database import Base, get_db
from server.app import app
from server.models.user import User
from server.models.session import Session as SessionModel
from server.models.run import Run
from server.models.conversation import Conversation
from server.auth.password import hash_password
from server.services.cache_service import cache_service


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
        "password": "TestPassword123!"
    }


@pytest.fixture
def test_user(db_session, test_user_data) -> User:
    """Create a test user in the database."""
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
    user = User(
        email="other@example.com",
        username="otheruser",
        password_hash=hash_password("OtherPassword123!"),
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
    user = User(
        email="inactive@example.com",
        username="inactiveuser",
        password_hash=hash_password("InactivePassword123!"),
        is_active=False,
        is_verified=True
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# Conversation fixtures
@pytest.fixture
def test_conversation(db_session, test_user) -> Conversation:
    """Create a test conversation."""
    from server.schemas.conversation import ConversationState
    conversation = Conversation(
        user_id=test_user.id,
        state=ConversationState.GREETING.value,
        context={},
        messages=[]
    )
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)
    return conversation


# Run fixtures
@pytest.fixture
def test_run(db_session, test_user) -> Run:
    """Create a test run."""
    run = Run(
        id="test-run-123",
        user_id=test_user.id,
        prompt="Test optimization prompt",
        status="pending",
        max_iterations=10,
        batch_size=5,
        metadata={}
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


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
