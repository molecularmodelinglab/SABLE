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
        r"(012|123|234|345|456|567|678|789|890)",  # Sequential numbers (length >= 3)
        r"(abc|bcd|cde|def|efg|fgh|ghi|hij|ijk|jkl|klm|lmn|mno|nop|opq|pqr|qrs|rst|stu|tuv|uvw|vwx|wxy|xyz)",  # Sequential letters (length >= 3)
        r"(password|qwerty|admin|letmein|welcome)",  # Common words (case insensitive)
    ]

    normalized = password.lower()
    leet_map = str.maketrans({
        "@": "a",
        "0": "o",
        "1": "l",
        "3": "e",
        "5": "s",
        "$": "s",
        "7": "t",
        "4": "a",
        "!": "i"
    })
    normalized_leet = normalized.translate(leet_map)
    alnum_normalized = re.sub(r"[^a-z0-9]", "", normalized_leet)

    for pattern in common_patterns:
        if re.search(pattern, normalized_leet, re.IGNORECASE):
            return (False, "Password contains common patterns and is too weak")

    # Check for common dictionary words after removing symbols
    common_words = ["password", "admin", "welcome", "letmein", "qwerty"]
    letters_only = re.sub(r"[^a-z]", "", normalized_leet)
    for word in common_words:
        if letters_only == word or letters_only == word * 2:
            return (False, "Password contains common patterns and is too weak")

    # Detect shorter sequential patterns (ascending/descending of length >= 2)
    sequences = [
        "0123456789",
        "9876543210",
        "abcdefghijklmnopqrstuvwxyz",
        "zyxwvutsrqponmlkjihgfedcba",
    ]

    for seq in sequences:
        for i in range(len(seq) - 2):
            segment = seq[i:i+3]
            if segment in normalized_leet or segment in alnum_normalized:
                return (False, "Password contains common patterns and is too weak")

    # Detect repeated characters (e.g., aaa)
    if re.search(r"(.)\1{2,}", normalized_leet):
        return (False, "Password contains common patterns and is too weak")

    return (True, "")


def generate_password_reset_token() -> str:
    """
    Generate a secure random token for password resets.

    Returns:
        URL-safe token string
    """
    import secrets
    return secrets.token_hex(32)


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
