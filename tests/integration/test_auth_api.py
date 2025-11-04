"""
Integration tests for authentication API endpoints.
"""
import pytest


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.auth
class TestAuthRegistration:
    """Tests for user registration endpoint."""
    
    def test_register_success(self, client):
        """Test successful user registration."""
        response = client.post(
            "/auth/register",
            json={
                "email": "newuser@example.com",
                "username": "newuser",
                "password": "S3cur3P@ss!"
            }
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "newuser@example.com"
        assert data["username"] == "newuser"
        assert "id" in data
        assert "password" not in data
        assert "password_hash" not in data
    
    def test_register_duplicate_email(self, client, test_user):
        """Test registration with duplicate email."""
        response = client.post(
            "/auth/register",
            json={
                "email": test_user.email,
                "username": "different",
                "password": "S3cur3P@ss!"
            }
        )
        
        assert response.status_code == 400
        assert "email" in response.json()["detail"].lower()
    
    def test_register_duplicate_username(self, client, test_user):
        """Test registration with duplicate username."""
        response = client.post(
            "/auth/register",
            json={
                "email": "different@example.com",
                "username": test_user.username,
                "password": "S3cur3P@ss!"
            }
        )
        
        assert response.status_code == 400
        assert "username" in response.json()["detail"].lower()
    
    def test_register_weak_password(self, client):
        """Test registration with weak password."""
        response = client.post(
            "/auth/register",
            json={
                "email": "user@example.com",
                "username": "username",
                "password": "weak"
            }
        )
        
        assert response.status_code == 400
        assert "password" in response.json()["detail"].lower()
    
    def test_register_invalid_email(self, client):
        """Test registration with invalid email."""
        response = client.post(
            "/auth/register",
            json={
                "email": "not-an-email",
                "username": "username",
                "password": "S3cur3P@ss!"
            }
        )
        
        assert response.status_code == 422  # Validation error


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.auth
class TestAuthLogin:
    """Tests for login endpoint."""
    
    def test_login_success(self, client, test_user, test_user_data):
        """Test successful login."""
        response = client.post(
            "/auth/login",
            json={
                "email": test_user_data["email"],
                "password": test_user_data["password"]
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "user" in data
        assert data["user"]["email"] == test_user.email
        assert "session" in data
    
    def test_login_wrong_password(self, client, test_user):
        """Test login with wrong password."""
        response = client.post(
            "/auth/login",
            json={
                "email": test_user.email,
                "password": "WrongP@ssw0rd9!"
            }
        )
        
        assert response.status_code == 401
        assert "invalid" in response.json()["detail"].lower()
    
    def test_login_nonexistent_user(self, client):
        """Test login with non-existent email."""
        response = client.post(
            "/auth/login",
            json={
                "email": "nonexistent@example.com",
                "password": "P@ssw0rd9!"
            }
        )
        
        assert response.status_code == 401
    
    def test_login_inactive_user(self, client, inactive_user):
        """Test login with inactive account."""
        response = client.post(
            "/auth/login",
            json={
                "email": inactive_user.email,
                "password": "InactiveP@ssw0rd9!"
            }
        )
        
        assert response.status_code == 403
        assert "inactive" in response.json()["detail"].lower()
    
    def test_login_rate_limiting(self, client):
        """Test rate limiting on failed login attempts."""
        email = "ratelimit@example.com"
        
        # Make 5 failed login attempts
        for _ in range(5):
            response = client.post(
                "/auth/login",
                json={
                    "email": email,
                    "password": "WrongP@ssw0rd9!"
                }
            )
        
        # 6th attempt should be rate limited
        response = client.post(
            "/auth/login",
            json={
                "email": email,
                "password": "WrongP@ssw0rd9!"
            }
        )
        
        assert response.status_code == 429
        assert "too many" in response.json()["detail"].lower()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.auth
class TestAuthProfile:
    """Tests for user profile endpoints."""
    
    def test_get_current_user(self, client, auth_headers, test_user):
        """Test getting current user info."""
        response = client.get("/auth/me", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == test_user.email
        assert data["username"] == test_user.username
        assert "password" not in data
    
    def test_get_current_user_no_auth(self, client):
        """Test getting current user without authentication."""
        response = client.get("/auth/me")
        
        assert response.status_code == 401
    
    def test_get_current_user_invalid_token(self, client):
        """Test with invalid token."""
        response = client.get(
            "/auth/me",
            headers={"Authorization": "Bearer invalid-token"}
        )
        
        assert response.status_code == 401


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.auth
class TestAuthLogout:
    """Tests for logout endpoints."""
    
    def test_logout_success(self, client, auth_headers):
        """Test successful logout."""
        response = client.post("/auth/logout", headers=auth_headers)
        
        assert response.status_code == 200
        assert "logged out" in response.json()["message"].lower()
    
    def test_logout_all_devices(self, client, auth_headers):
        """Test logout from all devices."""
        response = client.post("/auth/logout-all", headers=auth_headers)
        
        assert response.status_code == 200
        assert "all devices" in response.json()["message"].lower()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.auth
class TestPasswordChange:
    """Tests for password change endpoint."""
    
    def test_change_password_success(self, client, auth_headers, test_user_data):
        """Test successful password change."""
        response = client.post(
            "/auth/change-password",
            headers=auth_headers,
            json={
                "current_password": test_user_data["password"],
                "new_password": "N3wS3cur3!"
            }
        )
        
        assert response.status_code == 200
    
    def test_change_password_wrong_current(self, client, auth_headers):
        """Test password change with wrong current password."""
        response = client.post(
            "/auth/change-password",
            headers=auth_headers,
            json={
                "current_password": "WrongP@ssw0rd9!",
                "new_password": "N3wS3cur3!"
            }
        )
        
        assert response.status_code == 400
    
    def test_change_password_weak_new(self, client, auth_headers, test_user_data):
        """Test password change with weak new password."""
        response = client.post(
            "/auth/change-password",
            headers=auth_headers,
            json={
                "current_password": test_user_data["password"],
                "new_password": "weak"
            }
        )
        
        assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.auth
class TestAccountDeletion:
    """Tests for account deletion endpoint."""
    
    def test_delete_account(self, client, auth_headers):
        """Test account deletion."""
        response = client.delete("/auth/account", headers=auth_headers)
        
        assert response.status_code == 200
        assert "deleted" in response.json()["message"].lower()
        
        # Verify can't access with old token
        response = client.get("/auth/me", headers=auth_headers)
        assert response.status_code == 401
