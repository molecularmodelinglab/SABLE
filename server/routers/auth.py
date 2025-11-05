"""Authentication API endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from server.database import get_db
from server.models.user import User
from server.schemas.auth import (
    UserRegisterRequest,
    UserLoginRequest,
    UserLoginResponse,
    UserResponse,
    SessionResponse,
    SessionListResponse,
    PasswordChangeRequest,
    MessageResponse,
)
from server.services.auth_service import auth_service
from server.services.user_service import user_service
from server.auth.dependencies import get_current_user, get_current_active_user

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: UserRegisterRequest,
    db: Session = Depends(get_db)
):
    """
    Register a new user account.

    Creates a new user with email/password authentication.
    Email verification is required before full access (optional enforcement).

    **Requirements:**
    - Email must be unique and valid format
    - Username must be 3-50 characters, alphanumeric with _ and -
    - Password must meet strength requirements:
      - Minimum 8 characters
      - At least one uppercase letter
      - At least one lowercase letter
      - At least one digit
      - At least one special character

    **Returns:**
    - 201: User created successfully
    - 400: Validation failed or duplicate email/username
    """
    # Create user
    user, error = user_service.create_user(
        db=db,
        email=request.email,
        username=request.username,
        password=request.password,
        auth_provider="local"
    )

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )

    # TODO: Send verification email
    # from server.services.email_service import send_verification_email
    # send_verification_email(user.email, user.id)

    return UserResponse.model_validate(user)


@router.post("/login", response_model=UserLoginResponse)
async def login(
    request: UserLoginRequest,
    req: Request,
    db: Session = Depends(get_db)
):
    """
    Login with email and password.

    Authenticates user and creates a new session.

    **Rate Limiting:**
    - 5 attempts per email per 5 minutes
    - 10 attempts per IP per 5 minutes

    **Returns:**
    - 200: Login successful, returns JWT token and user info
    - 401: Invalid credentials
    - 429: Too many failed attempts (rate limited)
    """
    # Get client IP
    client_ip = req.client.host if req.client else None

    # Check rate limiting
    allowed, reason = auth_service.is_login_allowed(request.email, client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=reason
        )

    # Authenticate user
    user, error = auth_service.authenticate_user(
        db=db,
        email=request.email,
        password=request.password
    )

    if error:
        # Record failed attempt unless account is inactive (to avoid penalizing locked accounts)
        if "inactive" not in error.lower():
            auth_service.record_failed_login(request.email, client_ip)

        status_code = status.HTTP_403_FORBIDDEN if "inactive" in error.lower() else status.HTTP_401_UNAUTHORIZED

        raise HTTPException(
            status_code=status_code,
            detail=error
        )

    # Create session
    session_obj, jwt_token = auth_service.create_session(
        db=db,
        user=user,
        ip_address=client_ip,
        user_agent=req.headers.get("user-agent"),
        expiry_hours=24
    )

    return UserLoginResponse(
        access_token=jwt_token,
        token_type="bearer",
        user=UserResponse.model_validate(user),
        session=SessionResponse.model_validate(session_obj)
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Logout current user.

    Invalidates the current session.

    **Returns:**
    - 200: Logout successful
    - 401: Not authenticated
    """
    # Get all user sessions and invalidate the most recent one
    # (In a real implementation, we'd get the session from the token)
    sessions = auth_service.get_active_sessions(db, current_user)

    if sessions:
        auth_service.invalidate_session(db, sessions[0])

    return MessageResponse(
        message="Logged out successfully",
        success=True
    )


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Logout from all devices.

    Invalidates all active sessions for the current user.

    **Returns:**
    - 200: All sessions invalidated
    - 401: Not authenticated
    """
    count = auth_service.invalidate_all_user_sessions(db, current_user)

    return MessageResponse(
        message=f"Logged out from all devices ({count} session(s) invalidated)",
        success=True
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get current authenticated user information.

    **Returns:**
    - 200: Current user information
    - 401: Not authenticated
    - 403: Account inactive
    """
    return UserResponse.model_validate(current_user)


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all active sessions for current user.

    Useful for security auditing and managing active logins.

    **Returns:**
    - 200: List of active sessions
    - 401: Not authenticated
    """
    sessions = auth_service.get_active_sessions(db, current_user)

    return SessionListResponse(
        sessions=[SessionResponse.model_validate(s) for s in sessions],
        total=len(sessions)
    )


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    request: PasswordChangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Change user password.

    Requires current password for verification.

    **Returns:**
    - 200: Password changed successfully
    - 400: Invalid current password or weak new password
    - 401: Not authenticated
    """
    # Verify current password
    from server.auth.password import verify_password

    if not current_user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password authentication not set up for this account"
        )

    if not verify_password(request.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )

    # Change password
    try:
        user_service.change_password(
            db=db,
            user=current_user,
            new_password=request.new_password
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        ) from exc

    # Invalidate all other sessions for security
    auth_service.invalidate_all_user_sessions(db, current_user)

    return MessageResponse(
        message="Password changed successfully. Please log in again.",
        success=True
    )


@router.delete("/account", response_model=MessageResponse)
async def delete_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete user account.

    **WARNING:** This action is irreversible and will delete all user data.

    **Returns:**
    - 200: Account deleted successfully
    - 401: Not authenticated
    """
    # Invalidate all sessions
    auth_service.invalidate_all_user_sessions(db, current_user)

    # Delete user (cascades to related data)
    success = user_service.delete_user(db, current_user)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete account"
        )

    return MessageResponse(
        message="Account deleted successfully",
        success=True
    )


# TODO: Email verification endpoints
# @router.post("/verify-email")
# async def verify_email(token: str, db: Session = Depends(get_db)):
#     """Verify user email with token."""
#     pass

# TODO: Password reset endpoints
# @router.post("/forgot-password")
# async def forgot_password(email: str, db: Session = Depends(get_db)):
#     """Initiate password reset flow."""
#     pass
#
# @router.post("/reset-password")
# async def reset_password(token: str, new_password: str, db: Session = Depends(get_db)):
#     """Reset password with token."""
#     pass

# TODO: Auth0 OAuth endpoints
# @router.get("/auth0/login")
# async def auth0_login():
#     """Redirect to Auth0 login."""
#     pass
#
# @router.get("/auth0/callback")
# async def auth0_callback(code: str, state: str, db: Session = Depends(get_db)):
#     """Handle Auth0 callback."""
#     pass
