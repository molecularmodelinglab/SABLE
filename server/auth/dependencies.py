"""FastAPI dependencies for authentication and authorization."""

from typing import Optional, Iterable
from fastapi import Depends, HTTPException, status, Header, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from server.database import get_db
from server.models.user import User
from server.models.session import Session as SessionModel
from server.auth.jwt import verify_access_token
from server.services.cache_service import cache_service

# Security scheme for Swagger UI
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
    access_token_query: str | None = Query(default=None, alias="access_token")
) -> User:
    """
    Get current authenticated user from JWT token.

    This dependency can be used in any endpoint that requires authentication.

    Args:
        credentials: HTTP Bearer token from Authorization header
        db: Database session

    Returns:
        Current User object

    Raises:
        HTTPException: If token is invalid or user not found

    Example:
        @app.get("/protected")
        async def protected_route(user: User = Depends(get_current_user)):
            return {"message": f"Hello {user.username}"}
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token: str | None = None

    if credentials and credentials.credentials:
        token = credentials.credentials

    # Allow query parameter fallback for channels where headers are unavailable (e.g., SSE)
    if token is None and access_token_query:
        token = access_token_query

    if token is None:
        raise credentials_exception

    # Verify JWT token
    payload = verify_access_token(token)
    if payload is None:
        raise credentials_exception

    # Extract identifiers
    user_id: str = payload.get("user_id")
    session_id: str | None = payload.get("session_id")
    if user_id is None or session_id is None:
        raise credentials_exception

    # Validate session to enforce logout/invalidation semantics
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if (
        session is None
        or str(session.user_id) != user_id
        or not session.is_active
        or session.is_expired()
    ):
        # Ensure any cached session entry is cleared
        if session and session.token:
            cache_service.invalidate_session(session.token)
        raise credentials_exception

    # Try to get user from cache first
    cached_user_data = cache_service.get_cached_user(user_id)
    if cached_user_data:
        # Reconstruct user from cache (simplified)
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            return user

    # Get user from database
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception

    # Cache user for future requests
    cache_service.cache_user(str(user.id), user.to_dict(), ttl_minutes=60)

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Get current authenticated and active user.

    This dependency ensures the user account is active and verified.

    Args:
        current_user: Current user from get_current_user dependency

    Returns:
        Current active User object

    Raises:
        HTTPException: If user is inactive or not verified

    Example:
        @app.get("/active-only")
        async def active_route(user: User = Depends(get_current_active_user)):
            return {"message": f"Hello active user {user.username}"}
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account"
        )

    # Optionally enforce email verification
    # if not current_user.is_verified:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Email not verified"
    #     )

    return current_user


async def get_optional_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    Get current user if authenticated, None otherwise.

    This dependency allows endpoints to work with or without authentication.

    Args:
        authorization: Optional Authorization header
        db: Database session

    Returns:
        Current User object if authenticated, None otherwise

    Example:
        @app.get("/optional-auth")
        async def optional_route(user: Optional[User] = Depends(get_optional_current_user)):
            if user:
                return {"message": f"Hello {user.username}"}
            return {"message": "Hello guest"}
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    try:
        token = authorization.split(" ")[1]
        payload = verify_access_token(token)

        if payload is None:
            return None

        user_id: str = payload.get("user_id")
        if user_id is None:
            return None

        # Try cache first
        cached_user_data = cache_service.get_cached_user(user_id)
        if cached_user_data:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                return user

        # Get from database
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            cache_service.cache_user(str(user.id), user.to_dict())
            return user

    except Exception as e:
        print(f"Error in optional auth: {e}")

    return None


async def get_current_user_session(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> SessionModel:
    """
    Get current user's session from token.

    Args:
        credentials: HTTP Bearer token
        db: Database session

    Returns:
        Current Session object

    Raises:
        HTTPException: If session not found or invalid

    Example:
        @app.get("/session-info")
        async def session_route(session: SessionModel = Depends(get_current_user_session)):
            return {"session_id": session.id}
    """
    token = credentials.credentials

    # Check cache first
    cached_session = cache_service.get_cached_session(token)
    if cached_session:
        session = db.query(SessionModel).filter(
            SessionModel.id == cached_session["id"]
        ).first()
        if session and session.is_active and not session.is_expired():
            return session

    # Query database
    session = db.query(SessionModel).filter(
        SessionModel.token == token,
        SessionModel.is_active == True
    ).first()

    if not session or session.is_expired():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid"
        )

    # Cache session
    cache_service.cache_session(token, {
        "id": str(session.id),
        "user_id": str(session.user_id),
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat()
    })

    return session


def require_verified_email(user: User = Depends(get_current_user)) -> User:
    """
    Dependency that requires email to be verified.

    Args:
        user: Current user

    Returns:
        Verified user

    Raises:
        HTTPException: If email not verified

    Example:
        @app.post("/verified-only")
        async def verified_route(user: User = Depends(require_verified_email)):
            return {"message": "Email is verified"}
    """
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required. Please check your email."
        )
    return user


def _normalize_roles(*roles: str | Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for entry in roles:
        if isinstance(entry, str):
            value = entry.strip().lower()
            if value and value not in seen:
                seen.add(value)
                normalized.append(value)
        else:
            for item in entry:
                if isinstance(item, str):
                    value = item.strip().lower()
                    if value and value not in seen:
                        seen.add(value)
                        normalized.append(value)
    return normalized


def require_role(required_role: str):
    """Dependency factory enforcing that the current user has a specific role."""
    if not required_role or not isinstance(required_role, str):
        raise ValueError("required_role must be a non-empty string")

    normalized_role = required_role.strip().lower()

    async def role_checker(user: User = Depends(get_current_active_user)) -> User:
        if not user.has_role(normalized_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{required_role}' required"
            )
        return user

    return role_checker


def require_any_role(*roles: str):
    """Dependency factory enforcing that the current user has at least one of the roles."""
    normalized = _normalize_roles(*roles)
    if not normalized:
        raise ValueError("At least one role must be specified")

    async def role_checker(user: User = Depends(get_current_active_user)) -> User:
        if not user.has_role(normalized):
            joined = ", ".join(sorted(set(normalized)))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"One of the following roles is required: {joined}"
            )
        return user

    return role_checker


def get_current_admin(user: User = Depends(require_role("admin"))) -> User:
    """Dependency that ensures the current user is an administrator."""
    return user
