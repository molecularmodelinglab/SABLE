"""Password hashing and validation utilities."""

import re
from typing import Tuple
from passlib.context import CryptContext

# Configure bcrypt with cost factor 12 (good balance of security and performance)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt.

    Args:
        password: Plain text password

    Returns:
        Hashed password string

    Example:
        >>> hashed = hash_password("MySecurePassword123!")
        >>> print(hashed[:7])
        $2b$12$
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against its hash.

    Args:
        plain_password: Plain text password to verify
        hashed_password: Stored password hash

    Returns:
        True if password matches, False otherwise

    Example:
        >>> hashed = hash_password("MyPassword123!")
        >>> verify_password("MyPassword123!", hashed)
        True
        >>> verify_password("WrongPassword", hashed)
        False
    """
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def validate_password_strength(password: str) -> Tuple[bool, str]:
    """
    Validate password strength according to security requirements.

    Requirements:
    - Minimum 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character

    Args:
        password: Password to validate

    Returns:
        Tuple of (is_valid, error_message)

    Example:
        >>> validate_password_strength("weak")
        (False, "Password must be at least 8 characters long")
        >>> validate_password_strength("StrongPass123!")
        (True, "")
    """
    if len(password) < 8:
        return (False, "Password must be at least 8 characters long")

    if not re.search(r"[A-Z]", password):
        return (False, "Password must contain at least one uppercase letter")

    if not re.search(r"[a-z]", password):
        return (False, "Password must contain at least one lowercase letter")

    if not re.search(r"\d", password):
        return (False, "Password must contain at least one digit")

    if not re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\\/`~]', password):
        return (False, "Password must contain at least one special character")

    # Check for common weak patterns
    common_patterns = [
        r"(012|123|234|345|456|567|678|789|890)",  # Sequential numbers
        r"(abc|bcd|cde|def|efg|fgh|ghi|hij|ijk|jkl|klm|lmn|mno|nop|opq|pqr|qrs|rst|stu|tuv|uvw|vwx|wxy|xyz)",  # Sequential letters
        r"(password|qwerty|admin|letmein|welcome)",  # Common words (case insensitive)
    ]

    for pattern in common_patterns:
        if re.search(pattern, password, re.IGNORECASE):
            return (False, "Password contains common patterns and is too weak")

    return (True, "")


def generate_password_reset_token() -> str:
    """
    Generate a secure random token for password resets.

    Returns:
        URL-safe token string
    """
    import secrets
    return secrets.token_urlsafe(32)


def validate_password_reset_token(token: str) -> bool:
    """
    Validate password reset token format.

    Args:
        token: Token to validate

    Returns:
        True if token format is valid
    """
    # Token should be 32 bytes encoded as URL-safe base64 (43 characters)
    return len(token) >= 32 and re.match(r'^[A-Za-z0-9_-]+$', token) is not None
