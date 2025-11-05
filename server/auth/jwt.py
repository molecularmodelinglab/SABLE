"""JWT token creation and verification."""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

# JWT Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "change_this_secret_key_in_production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "24"))


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
    token_type: str = "access"
) -> str:
    """
    Create a JWT access token.

    Args:
        data: Payload data to encode in the token
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT token string

    Example:
        >>> token = create_access_token({"sub": "user@example.com", "user_id": "123"})
        >>> print(token[:20])
        eyJhbGciOiJIUzI1NiIs...
    """
    to_encode = data.copy()

    # Set expiration
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)

    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),  # Issued at
        "type": token_type
    })

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_access_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify and decode a JWT access token.

    Args:
        token: JWT token string

    Returns:
        Decoded token payload if valid, None if invalid or expired

    Example:
        >>> token = create_access_token({"sub": "user@example.com"})
        >>> payload = verify_access_token(token)
        >>> print(payload["sub"])
        user@example.com
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        # Verify token type
        if payload.get("type") != "access":
            return None

        return payload
    except ExpiredSignatureError:
        print("Token has expired")
        return None
    except JWTError as e:
        print(f"Token verification failed: {e}")
        return None


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Decode a JWT token without verification (use with caution).

    Args:
        token: JWT token string

    Returns:
        Decoded token payload if parseable, None otherwise
    """
    try:
        # Decode without verification to inspect token contents
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"verify_signature": False}
        )
        return payload
    except JWTError as e:
        print(f"Token decoding failed: {e}")
        return None


def create_refresh_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create a JWT refresh token (longer expiration).

    Args:
        data: Payload data to encode
        expires_delta: Optional custom expiration (default: 7 days)

    Returns:
        Encoded JWT refresh token string
    """
    to_encode = data.copy()

    # Refresh tokens have longer expiration
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=7)

    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh"
    })

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_refresh_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify and decode a JWT refresh token.

    Args:
        token: JWT refresh token string

    Returns:
        Decoded token payload if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        # Verify token type
        if payload.get("type") != "refresh":
            return None

        return payload
    except ExpiredSignatureError:
        print("Refresh token has expired")
        return None
    except JWTError as e:
        print(f"Refresh token verification failed: {e}")
        return None


def get_token_expiration(token_or_payload: Any) -> Optional[datetime]:
    """
    Extract expiration time from token.

    Args:
        token: JWT token string

    Returns:
        Expiration datetime if valid, None otherwise
    """
    if isinstance(token_or_payload, dict):
        payload = token_or_payload
    elif isinstance(token_or_payload, str):
        payload = decode_token(token_or_payload)
    else:
        return None

    if not payload or "exp" not in payload:
        return None

    exp_value = payload["exp"]
    if isinstance(exp_value, datetime):
        return exp_value if exp_value.tzinfo else exp_value.replace(tzinfo=timezone.utc)

    try:
        return datetime.fromtimestamp(exp_value, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def is_token_expired(token_or_payload: Any) -> bool:
    """
    Check if token is expired.

    Args:
        token: JWT token string

    Returns:
        True if expired or invalid, False if still valid
    """
    expiration = get_token_expiration(token_or_payload)
    if expiration:
        return datetime.now(timezone.utc) > expiration
    return True


def create_email_verification_token(email: str) -> str:
    """
    Create a token for email verification.

    Args:
        email: User email address

    Returns:
        Email verification token
    """
    data = {
        "sub": email,
        "type": "email_verification"
    }
    # Email verification tokens expire in 24 hours
    return create_access_token(
        data,
        expires_delta=timedelta(hours=24),
        token_type="email_verification"
    )


def verify_email_verification_token(token: str) -> Optional[str]:
    """
    Verify email verification token and extract email.

    Args:
        token: Email verification token

    Returns:
        Email address if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        if payload.get("type") != "email_verification":
            return None

        return payload.get("sub")
    except JWTError:
        return None
