"""
Unit tests for user service.
"""
import pytest
from server.services.user_service import user_service
from server.models.user import User
from server.auth.password import verify_password


@pytest.mark.unit
@pytest.mark.database
class TestUserService:
    """Tests for user service operations."""
    
    def test_create_user(self, db_session):
        """Test user creation."""
        email = "newuser@example.com"
        username = "newuser"
        password = "NewP@ssw0JIrd9YAY!"
        
        user, error = user_service.create_user(
            db_session, email, username, password
        )
        
        assert error is None
        assert user is not None
        assert user.email == email
        assert user.username == username
        assert user.is_active is True
        assert user.is_verified is False
        assert verify_password(password, user.password_hash)
        assert user.roles == []

    def test_create_user_with_roles(self, db_session):
        """User creation should store normalized, unique role assignments."""
        email = "admin2@example.com"
        username = "admin2"
        password = "Adm1n#SecureR0les42"

        user, error = user_service.create_user(
            db_session,
            email,
            username,
            password,
            roles=["Admin", "observer", "admin"],
        )

        assert error is None
        assert user is not None
        assert user.roles == ["admin", "observer"]
    
    def test_create_user_duplicate_email(self, db_session, test_user):
        """Test creating user with duplicate email."""
        user, error = user_service.create_user(
            db_session, test_user.email, "different", "P@ssw0rd9!"
        )
        
        assert user is None
        assert error is not None
        assert "email" in error.lower()
    
    def test_create_user_duplicate_username(self, db_session, test_user):
        """Test creating user with duplicate username."""
        user, error = user_service.create_user(
            db_session, "different@example.com", test_user.username, "P@ssw0rd9!"
        )
        
        assert user is None
        assert error is not None
        assert "username" in error.lower()
    
    def test_create_user_weak_password(self, db_session):
        """Test creating user with weak password."""
        user, error = user_service.create_user(
            db_session, "user@example.com", "username", "weak"
        )
        
        assert user is None
        assert error is not None
        assert "password" in error.lower()
    
    def test_get_user_by_id(self, db_session, test_user):
        """Test getting user by ID."""
        user = user_service.get_user_by_id(db_session, str(test_user.id))
        
        assert user is not None
        assert user.id == test_user.id
        assert user.email == test_user.email
    
    def test_get_user_by_id_not_found(self, db_session):
        """Test getting non-existent user by ID."""
        from uuid import uuid4
        user = user_service.get_user_by_id(db_session, str(uuid4()))
        
        assert user is None
    
    def test_get_user_by_email(self, db_session, test_user):
        """Test getting user by email."""
        user = user_service.get_user_by_email(db_session, test_user.email)
        
        assert user is not None
        assert user.id == test_user.id
    
    def test_get_user_by_email_case_insensitive(self, db_session, test_user):
        """Test email lookup is case-insensitive."""
        user = user_service.get_user_by_email(
            db_session, test_user.email.upper()
        )
        
        assert user is not None
        assert user.id == test_user.id
    
    def test_get_user_by_username(self, db_session, test_user):
        """Test getting user by username."""
        user = user_service.get_user_by_username(db_session, test_user.username)
        
        assert user is not None
        assert user.id == test_user.id
    
    def test_update_user(self, db_session, test_user):
        """Test updating user information."""
        updates = {
            "username": "updatedusername",
            "email": "updated@example.com"
        }
        
        user = user_service.update_user(db_session, test_user, updates)
        
        assert user.username == updates["username"]
        assert user.email == updates["email"]
    
    def test_verify_email(self, db_session, test_user):
        """Test email verification."""
        # Make user unverified first
        test_user.is_verified = False
        db_session.commit()
        
        user = user_service.verify_email(db_session, test_user)
        
        assert user.is_verified is True
    
    def test_activate_user(self, db_session, inactive_user):
        """Test user activation."""
        user = user_service.activate_user(db_session, inactive_user)
        
        assert user.is_active is True
    
    def test_deactivate_user(self, db_session, test_user):
        """Test user deactivation."""
        user = user_service.deactivate_user(db_session, test_user)
        
        assert user.is_active is False
    
    def test_update_last_login(self, db_session, test_user):
        """Test updating last login timestamp."""
        original_last_login = test_user.last_login_at
        
        user = user_service.update_last_login(db_session, test_user)
        
        assert user.last_login_at is not None
        assert user.last_login_at != original_last_login
    
    def test_change_password(self, db_session, test_user):
        """Test password change."""
        new_password = "N3wS3cur3!"
        
        user = user_service.change_password(
            db_session, test_user, new_password
        )
        
        assert verify_password(new_password, user.password_hash)
    
    def test_delete_user(self, db_session, test_user):
        """Test user deletion."""
        user_id = test_user.id
        
        user_service.delete_user(db_session, test_user)
        
        # Verify user is deleted
        deleted_user = user_service.get_user_by_id(db_session, str(user_id))
        assert deleted_user is None
