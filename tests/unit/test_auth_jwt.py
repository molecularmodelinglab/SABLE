"""
Unit tests for JWT token management.
"""
import pytest
from datetime import datetime, timedelta, timezone
from server.auth.jwt import (
    create_access_token,
    create_refresh_token,
    create_email_verification_token,
    decode_token,
    is_token_expired
)


class TestAccessToken:
    """Tests for access token creation and validation."""
    
    def test_create_access_token(self):
        """Test access token creation."""
        data = {"sub": "user@example.com", "user_id": "user-123"}
        token = create_access_token(data)
        
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0
    
    def test_decode_access_token(self):
        """Test access token decoding."""
        data = {"sub": "user@example.com", "user_id": "user-123"}
        token = create_access_token(data)
        
        payload = decode_token(token)
        
        assert payload is not None
        assert payload["sub"] == "user@example.com"
        assert payload["user_id"] == "user-123"
        assert payload["type"] == "access"
        assert "exp" in payload
    
    def test_access_token_with_custom_expiry(self):
        """Test access token with custom expiration."""
        data = {"sub": "user@example.com", "user_id": "user-123"}
        expires_delta = timedelta(minutes=15)
        
        token = create_access_token(data, expires_delta=expires_delta)
        payload = decode_token(token)
        
        assert payload is not None
        # Check expiration is roughly 15 minutes from now
        exp_time = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        expected_exp = datetime.now(timezone.utc) + expires_delta
        
        assert abs((exp_time - expected_exp).total_seconds()) < 5
    
    def test_decode_invalid_token(self):
        """Test decoding invalid token."""
        invalid_token = "invalid.token.here"
        
        payload = decode_token(invalid_token)
        
        assert payload is None
    
    def test_decode_malformed_token(self):
        """Test decoding malformed token."""
        malformed_token = "not-a-jwt-token"
        
        payload = decode_token(malformed_token)
        
        assert payload is None


class TestRefreshToken:
    """Tests for refresh token creation."""
    
    def test_create_refresh_token(self):
        """Test refresh token creation."""
        data = {"sub": "user@example.com", "user_id": "user-123"}
        token = create_refresh_token(data)
        
        assert token is not None
        assert isinstance(token, str)
    
    def test_decode_refresh_token(self):
        """Test refresh token decoding."""
        data = {"sub": "user@example.com", "user_id": "user-123"}
        token = create_refresh_token(data)
        
        payload = decode_token(token)
        
        assert payload is not None
        assert payload["sub"] == "user@example.com"
        assert payload["type"] == "refresh"


class TestEmailVerificationToken:
    """Tests for email verification token."""
    
    def test_create_verification_token(self):
        """Test email verification token creation."""
        email = "test@example.com"
        token = create_email_verification_token(email)
        
        assert token is not None
        assert isinstance(token, str)
    
    def test_decode_verification_token(self):
        """Test verification token decoding."""
        email = "test@example.com"
        token = create_email_verification_token(email)
        
        payload = decode_token(token)
        
        assert payload is not None
        assert payload["sub"] == email
        assert payload["type"] == "email_verification"


class TestTokenExpiration:
    """Tests for token expiration checking."""
    
    def test_token_not_expired(self):
        """Test that fresh token is not expired."""
        data = {"sub": "user@example.com", "user_id": "user-123"}
        token = create_access_token(data)
        payload = decode_token(token)
        
        assert is_token_expired(payload) is False
    
    def test_token_expired(self):
        """Test expired token detection."""
        # Create token that expired 1 hour ago
        expires_delta = timedelta(hours=-1)
        data = {"sub": "user@example.com", "user_id": "user-123"}
        token = create_access_token(data, expires_delta=expires_delta)
        payload = decode_token(token)
        
        assert is_token_expired(payload) is True
    
    def test_token_expiration_edge_case(self):
        """Test token expiring in 1 second."""
        expires_delta = timedelta(seconds=1)
        data = {"sub": "user@example.com", "user_id": "user-123"}
        token = create_access_token(data, expires_delta=expires_delta)
        payload = decode_token(token)
        
        # Should not be expired yet
        assert is_token_expired(payload) is False
