"""
Unit tests for password hashing and validation.
"""
import pytest
from server.auth.password import (
    hash_password,
    verify_password,
    validate_password_strength,
    generate_password_reset_token
)


class TestPasswordHashing:
    """Tests for password hashing functions."""
    
    def test_hash_password(self):
        """Test password hashing."""
        password = "S3cur3P@ss!"
        hashed = hash_password(password)
        
        assert hashed is not None
        assert hashed != password
        assert hashed.startswith("$2b$")  # bcrypt prefix
    
    def test_hash_password_different_hashes(self):
        """Test that same password generates different hashes (salt)."""
        password = "S3cur3P@ss!"
        hash1 = hash_password(password)
        hash2 = hash_password(password)
        
        assert hash1 != hash2  # Different salts
    
    def test_verify_password_correct(self):
        """Test password verification with correct password."""
        password = "S3cur3P@ss!"
        hashed = hash_password(password)
        
        assert verify_password(password, hashed) is True
    
    def test_verify_password_incorrect(self):
        """Test password verification with incorrect password."""
        password = "S3cur3P@ss!"
        wrong_password = "Wr0ngP@ss!"
        hashed = hash_password(password)
        
        assert verify_password(wrong_password, hashed) is False
    
    def test_verify_password_empty(self):
        """Test password verification with empty password."""
        hashed = hash_password("S3cur3P@ss!")
        
        assert verify_password("", hashed) is False


class TestPasswordStrength:
    """Tests for password strength validation."""
    
    def test_valid_strong_password(self):
        """Test validation of strong password."""
        password = "S3cur3P@ss!"
        is_valid, message = validate_password_strength(password)
        
        assert is_valid is True
        assert message is None
    
    def test_password_too_short(self):
        """Test password that's too short."""
        password = "Short1!"
        is_valid, message = validate_password_strength(password)
        
        assert is_valid is False
        assert "at least 8 characters" in message
    
    def test_password_no_uppercase(self):
        """Test password without uppercase letter."""
        password = "p@ssw0rd!"
        is_valid, message = validate_password_strength(password)
        
        assert is_valid is False
        assert "uppercase" in message
    
    def test_password_no_lowercase(self):
        """Test password without lowercase letter."""
        password = "P@SSW0RD!"
        is_valid, message = validate_password_strength(password)
        
        assert is_valid is False
        assert "lowercase" in message
    
    def test_password_no_digit(self):
        """Test password without digit."""
        password = "PasswordOnly!"
        is_valid, message = validate_password_strength(password)
        
        assert is_valid is False
        assert "digit" in message
    
    def test_password_no_special_char(self):
        """Test password without special character."""
        password = "Password123"
        is_valid, message = validate_password_strength(password)
        
        assert is_valid is False
        assert "special character" in message
    
    def test_password_common_pattern_sequential(self):
        """Test password with sequential numbers."""
        password = "P@ssw0rd89!"
        is_valid, message = validate_password_strength(password)
        
        assert is_valid is False
        assert "common pattern" in message
    
    def test_password_common_pattern_repeated(self):
        """Test password with repeated characters."""
        password = "Passwordaaa1!"
        is_valid, message = validate_password_strength(password)
        
        assert is_valid is False
        assert "common pattern" in message
    
    def test_password_common_word(self):
        """Test password with common word."""
        password = "P@ssw0rd9!"
        is_valid, message = validate_password_strength(password)
        
        assert is_valid is False
        assert "common pattern" in message.lower()


class TestPasswordResetToken:
    """Tests for password reset token generation."""
    
    def test_generate_reset_token(self):
        """Test reset token generation."""
        token = generate_password_reset_token()
        
        assert token is not None
        assert len(token) == 64  # 32 bytes hex = 64 characters
        assert all(c in '0123456789abcdef' for c in token)
    
    def test_generate_different_tokens(self):
        """Test that different tokens are generated."""
        token1 = generate_password_reset_token()
        token2 = generate_password_reset_token()
        
        assert token1 != token2
