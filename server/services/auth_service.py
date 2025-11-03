"""Authentication service layer."""

import secrets
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from server.models.user import User
from server.models.session import Session as SessionModel
from server.auth.password import verify_password
from server.auth.jwt import create_access_token
from server.services.cache_service import cache_service
from server.services.user_service import user_service


class AuthService:
    """Service for authentication operations."""

    def authenticate_user(
        self,
        db: Session,
        email: str,
        password: str
    ) -> tuple[Optional[User], Optional[str]]:
        """
        Authenticate user with email and password.

        Args:
            db: Database session
            email: User email address
            password: Plain text password

        Returns:
            Tuple of (User object, error message). If successful, error is None.

        Example:
            >>> service = AuthService()
            >>> user, error = service.authenticate_user(db, "user@example.com", "password123")
            >>> if user:
            ...     print(f"Authenticated: {user.username}")
        """
        # Get user by email
        user = user_service.get_user_by_email(db, email)
        if not user:
            return (None, "Invalid email or password")

        # Check if account is active
        if not user.is_active:
            return (None, "Account is inactive")

        # Verify password
        if user.auth_provider == "local":
            if not user.password_hash:
                return (None, "Account not set up for password login")

            if not verify_password(password, user.password_hash):
                return (None, "Invalid email or password")
        else:
            return (None, f"Please login with {user.auth_provider}")

        return (user, None)

    def create_session(
        self,
        db: Session,
        user: User,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        expiry_hours: int = 24
    ) -> tuple[SessionModel, str]:
        """
        Create a new session for user.

        Args:
            db: Database session
            user: User object
            ip_address: Client IP address
            user_agent: Client user agent string
            expiry_hours: Session expiry in hours

        Returns:
            Tuple of (Session object, JWT token)

        Example:
            >>> session, token = service.create_session(db, user, "192.168.1.1")
            >>> print(f"Token: {token[:20]}...")
        """
        # Generate secure session token
        session_token = secrets.token_urlsafe(32)

        # Create session in database
        session = SessionModel(
            user_id=user.id,
            token=session_token,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
            is_active=True
        )

        db.add(session)
        db.commit()
        db.refresh(session)

        # Create JWT token
        token_data = {
            "sub": user.email,
            "user_id": str(user.id),
            "username": user.username,
            "session_id": str(session.id)
        }
        jwt_token = create_access_token(token_data, expires_delta=timedelta(hours=expiry_hours))

        # Cache session for fast lookups
        cache_service.cache_session(session_token, {
            "id": str(session.id),
            "user_id": str(user.id),
            "email": user.email,
            "username": user.username,
            "created_at": session.created_at.isoformat(),
            "expires_at": session.expires_at.isoformat()
        }, ttl_hours=expiry_hours)

        # Update last login
        user_service.update_last_login(db, user)

        return (session, jwt_token)

    def invalidate_session(
        self,
        db: Session,
        session: SessionModel
    ) -> bool:
        """
        Invalidate a session (logout).

        Args:
            db: Database session
            session: Session object to invalidate

        Returns:
            True if invalidated successfully
        """
        try:
            session.is_active = False
            db.commit()

            # Remove from cache
            cache_service.invalidate_session(session.token)

            return True
        except Exception as e:
            db.rollback()
            print(f"Error invalidating session: {e}")
            return False

    def invalidate_all_user_sessions(
        self,
        db: Session,
        user: User
    ) -> int:
        """
        Invalidate all sessions for a user.

        Useful for forcing logout from all devices.

        Args:
            db: Database session
            user: User object

        Returns:
            Number of sessions invalidated
        """
        try:
            sessions = db.query(SessionModel).filter(
                SessionModel.user_id == user.id,
                SessionModel.is_active == True
            ).all()

            count = 0
            for session in sessions:
                session.is_active = False
                cache_service.invalidate_session(session.token)
                count += 1

            db.commit()
            return count

        except Exception as e:
            db.rollback()
            print(f"Error invalidating user sessions: {e}")
            return 0

    def refresh_session(
        self,
        db: Session,
        session: SessionModel,
        extend_hours: int = 24
    ) -> SessionModel:
        """
        Refresh session expiration.

        Args:
            db: Database session
            session: Session object
            extend_hours: Hours to extend expiration

        Returns:
            Updated session object
        """
        session.last_activity = datetime.now(timezone.utc)
        session.expires_at = datetime.now(timezone.utc) + timedelta(hours=extend_hours)
        db.commit()
        db.refresh(session)

        # Update cache TTL
        cache_service.refresh_session_ttl(session.token, ttl_hours=extend_hours)

        return session

    def get_active_sessions(
        self,
        db: Session,
        user: User
    ) -> list[SessionModel]:
        """
        Get all active sessions for a user.

        Args:
            db: Database session
            user: User object

        Returns:
            List of active Session objects
        """
        return db.query(SessionModel).filter(
            SessionModel.user_id == user.id,
            SessionModel.is_active == True
        ).order_by(SessionModel.last_activity.desc()).all()

    def cleanup_expired_sessions(self, db: Session) -> int:
        """
        Clean up expired sessions from database.

        Should be run periodically (e.g., daily cron job).

        Args:
            db: Database session

        Returns:
            Number of sessions cleaned up
        """
        try:
            now = datetime.now(timezone.utc)
            expired_sessions = db.query(SessionModel).filter(
                SessionModel.expires_at < now
            ).all()

            count = 0
            for session in expired_sessions:
                db.delete(session)
                cache_service.invalidate_session(session.token)
                count += 1

            db.commit()
            return count

        except Exception as e:
            db.rollback()
            print(f"Error cleaning up sessions: {e}")
            return 0

    def check_rate_limit(
        self,
        identifier: str,
        limit: int = 5,
        window_seconds: int = 60
    ) -> tuple[bool, int, int]:
        """
        Check rate limit for authentication attempts.

        Args:
            identifier: Identifier (email or IP address)
            limit: Maximum attempts allowed
            window_seconds: Time window in seconds

        Returns:
            Tuple of (allowed, current_count, remaining)

        Example:
            >>> allowed, count, remaining = service.check_rate_limit("user@example.com")
            >>> if not allowed:
            ...     print(f"Rate limit exceeded. Try again later.")
        """
        return cache_service.check_rate_limit(
            identifier,
            limit=limit,
            window_seconds=window_seconds,
            namespace="auth"
        )

    def record_failed_login(
        self,
        email: str,
        ip_address: Optional[str] = None
    ) -> None:
        """
        Record failed login attempt for rate limiting.

        Args:
            email: Email address
            ip_address: Optional IP address
        """
        # Rate limit by email
        self.check_rate_limit(f"login:{email}", limit=5, window_seconds=300)

        # Also rate limit by IP if provided
        if ip_address:
            self.check_rate_limit(f"login:ip:{ip_address}", limit=10, window_seconds=300)

    def is_login_allowed(
        self,
        email: str,
        ip_address: Optional[str] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Check if login is allowed (not rate limited).

        Args:
            email: Email address
            ip_address: Optional IP address

        Returns:
            Tuple of (allowed, reason)
        """
        # Check email rate limit
        allowed_email, count_email, _ = self.check_rate_limit(
            f"login:{email}",
            limit=5,
            window_seconds=300
        )

        if not allowed_email:
            return (False, f"Too many failed login attempts. Please try again in 5 minutes.")

        # Check IP rate limit if provided
        if ip_address:
            allowed_ip, count_ip, _ = self.check_rate_limit(
                f"login:ip:{ip_address}",
                limit=10,
                window_seconds=300
            )

            if not allowed_ip:
                return (False, f"Too many login attempts from this IP. Please try again in 5 minutes.")

        return (True, None)

    def create_auth0_user(
        self,
        db: Session,
        auth0_user_id: str,
        email: str,
        username: str,
        user_info: Dict[str, Any]
    ) -> tuple[Optional[User], Optional[str]]:
        """
        Create or update user from Auth0 authentication.

        Args:
            db: Database session
            auth0_user_id: Auth0 user ID (from "sub" claim)
            email: User email
            username: Username
            user_info: Additional user information from Auth0

        Returns:
            Tuple of (User object, error message)
        """
        # Check if user already exists
        user = db.query(User).filter(User.auth0_user_id == auth0_user_id).first()

        if user:
            # Update existing user
            user.email = email.lower()
            user.username = username
            user.is_verified = user_info.get("email_verified", False)
            user.updated_at = datetime.now(timezone.utc)
            user.extra_metadata = user_info
            db.commit()
            db.refresh(user)
            return (user, None)

        # Check if email already exists with different provider
        existing_user = user_service.get_user_by_email(db, email)
        if existing_user:
            return (None, "Email already registered with different authentication method")

        # Create new user
        try:
            user = User(
                email=email.lower(),
                username=username,
                password_hash=None,
                auth_provider="auth0",
                auth0_user_id=auth0_user_id,
                is_active=True,
                is_verified=user_info.get("email_verified", False),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                extra_metadata=user_info
            )

            db.add(user)
            db.commit()
            db.refresh(user)

            return (user, None)

        except Exception as e:
            db.rollback()
            print(f"Error creating Auth0 user: {e}")
            return (None, "Failed to create user account")


# Global auth service instance
auth_service = AuthService()
