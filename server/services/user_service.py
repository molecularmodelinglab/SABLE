"""User management service layer."""

from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from server.models.user import User
from server.auth.password import hash_password, validate_password_strength
from server.services.cache_service import cache_service


class UserService:
    """Service for user management operations."""

    def create_user(
        self,
        db: Session,
        email: str,
        username: str,
        password: str,
        auth_provider: str = "local"
    ) -> tuple[Optional[User], Optional[str]]:
        """
        Create a new user account.

        Args:
            db: Database session
            email: User email address
            username: Username
            password: Plain text password
            auth_provider: Authentication provider ("local" or "auth0")

        Returns:
            Tuple of (User object, error message). If successful, error is None.

        Example:
            >>> service = UserService()
            >>> user, error = service.create_user(db, "user@example.com", "johndoe", "SecurePass123!")
            >>> if user:
            ...     print(f"Created user: {user.username}")
        """
        # Validate email format
        if "@" not in email or "." not in email.split("@")[-1]:
            return (None, "Invalid email address format")

        # Validate username
        if len(username) < 3:
            return (None, "Username must be at least 3 characters long")

        if len(username) > 50:
            return (None, "Username must be less than 50 characters")

        # Enforce uniqueness before attempting insert for clearer errors
        if self.get_user_by_email(db, email):
            return (None, "Email address already registered")

        if self.get_user_by_username(db, username):
            return (None, "Username already taken")

        # Validate password strength for local auth
        if auth_provider == "local":
            is_valid, error_msg = validate_password_strength(password)
            if not is_valid:
                return (None, error_msg)

        # Hash password
        password_hash = hash_password(password) if auth_provider == "local" else None

        # Create user
        try:
            user = User(
                email=email.lower(),
                username=username,
                password_hash=password_hash,
                auth_provider=auth_provider,
                is_active=True,
                is_verified=False,  # Require email verification
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            db.add(user)
            db.commit()
            db.refresh(user)

            return (user, None)

        except IntegrityError as e:
            db.rollback()

            detail = str(e).lower()
            constraint_name = None
            if hasattr(e, "orig") and hasattr(e.orig, "diag") and getattr(e.orig.diag, "constraint_name", None):
                constraint_name = e.orig.diag.constraint_name.lower()

            if constraint_name and "email" in constraint_name:
                return (None, "Email address already registered")
            if "email" in detail:
                return (None, "Email address already registered")

            if constraint_name and "username" in constraint_name:
                return (None, "Username already taken")
            if "username" in detail:
                return (None, "Username already taken")

            return (None, "Failed to create user account")

    def get_user_by_id(self, db: Session, user_id: str) -> Optional[User]:
        """
        Get user by ID.

        Args:
            db: Database session
            user_id: User UUID

        Returns:
            User object if found, None otherwise
        """
        # Try cache first
        cached_user = cache_service.get_cached_user(user_id)
        if cached_user:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                return user

        # Query database
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            cache_service.cache_user(str(user.id), user.to_dict())
        return user

    def get_user_by_email(self, db: Session, email: str) -> Optional[User]:
        """
        Get user by email address.

        Args:
            db: Database session
            email: Email address

        Returns:
            User object if found, None otherwise
        """
        return db.query(User).filter(User.email == email.lower()).first()

    def get_user_by_username(self, db: Session, username: str) -> Optional[User]:
        """
        Get user by username.

        Args:
            db: Database session
            username: Username

        Returns:
            User object if found, None otherwise
        """
        return db.query(User).filter(User.username == username).first()

    def update_user(
        self,
        db: Session,
        user: User,
        updates: Optional[dict] = None,
        **kwargs
    ) -> User:
        """
        Update user fields.

        Args:
            db: Database session
            user: User object to update
            updates: Optional dictionary of fields to update
            **kwargs: Additional fields to update

        Returns:
            Updated User object

        Raises:
            ValueError: If an update violates uniqueness or other constraints
        """
        changes: dict = {}
        if updates:
            changes.update(updates)
        if kwargs:
            changes.update(kwargs)

        if not changes:
            return user

        # Normalize and validate email/username updates before applying
        if "email" in changes and changes["email"]:
            new_email = changes["email"].lower()
            existing = self.get_user_by_email(db, new_email)
            if existing and existing.id != user.id:
                raise ValueError("Email address already registered")
            changes["email"] = new_email

        if "username" in changes and changes["username"]:
            existing = self.get_user_by_username(db, changes["username"])
            if existing and existing.id != user.id:
                raise ValueError("Username already taken")

        try:
            for key, value in changes.items():
                if hasattr(user, key):
                    setattr(user, key, value)

            user.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(user)

        except IntegrityError as exc:
            db.rollback()
            detail = str(exc).lower()
            if "email" in detail:
                raise ValueError("Email address already registered") from exc
            if "username" in detail:
                raise ValueError("Username already taken") from exc
            raise ValueError("Update failed: duplicate value") from exc

        # Invalidate cache so subsequent reads see fresh data
        cache_service.invalidate_user(str(user.id))

        return user

    def verify_email(self, db: Session, user: User) -> User:
        """
        Mark user's email as verified.

        Args:
            db: Database session
            user: User object

        Returns:
            Updated user object
        """
        user.is_verified = True
        user.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)

        # Invalidate cache
        cache_service.invalidate_user(str(user.id))

        return user

    def deactivate_user(self, db: Session, user: User) -> User:
        """
        Deactivate user account.

        Args:
            db: Database session
            user: User object

        Returns:
            Updated user object
        """
        user.is_active = False
        user.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)

        # Invalidate cache
        cache_service.invalidate_user(str(user.id))

        return user

    def activate_user(self, db: Session, user: User) -> User:
        """
        Activate user account.

        Args:
            db: Database session
            user: User object

        Returns:
            Updated user object
        """
        user.is_active = True
        user.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)

        # Invalidate cache
        cache_service.invalidate_user(str(user.id))

        return user

    def update_last_login(self, db: Session, user: User) -> User:
        """
        Update user's last login timestamp.

        Args:
            db: Database session
            user: User object

        Returns:
            Updated user object
        """
        user.last_login = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)

        # Update cache
        cache_service.cache_user(str(user.id), user.to_dict())

        return user

    def change_password(
        self,
        db: Session,
        user: User,
        new_password: str
    ) -> User:
        """
        Change user's password.

        Args:
            db: Database session
            user: User object
            new_password: New plain text password

        Returns:
            Updated User instance

        Raises:
            ValueError: If the new password does not meet strength requirements
        """
        # Validate new password
        is_valid, error_msg = validate_password_strength(new_password)
        if not is_valid:
            raise ValueError(error_msg)

        # Hash and update
        user.password_hash = hash_password(new_password)
        user.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)

        # Invalidate cache
        cache_service.invalidate_user(str(user.id))

        return user

    def delete_user(self, db: Session, user: User) -> bool:
        """
        Delete user account (use with caution).

        Args:
            db: Database session
            user: User object

        Returns:
            True if deleted successfully
        """
        try:
            db.delete(user)
            db.commit()

            # Invalidate cache
            cache_service.invalidate_user(str(user.id))

            return True
        except Exception as e:
            db.rollback()
            print(f"Error deleting user: {e}")
            return False

    def list_users(
        self,
        db: Session,
        skip: int = 0,
        limit: int = 100,
        is_active: Optional[bool] = None
    ) -> List[User]:
        """
        List users with pagination.

        Args:
            db: Database session
            skip: Number of records to skip
            limit: Maximum number of records to return
            is_active: Filter by active status

        Returns:
            List of User objects
        """
        query = db.query(User)

        if is_active is not None:
            query = query.filter(User.is_active == is_active)

        return query.offset(skip).limit(limit).all()


# Global user service instance
user_service = UserService()
