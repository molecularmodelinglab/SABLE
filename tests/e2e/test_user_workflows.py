"""
End-to-end tests for complete user workflows.
"""
import pytest


@pytest.mark.e2e
class TestCompleteUserJourney:
    """Test complete user journey from registration to run completion."""
    
    def test_register_login_create_run(self, client):
        """Test: Register → Login → Create Run."""
        # Step 1: Register
        register_response = client.post(
            "/auth/register",
            json={
                "email": "e2euser@example.com",
                "username": "e2euser",
                "password": "E2EPassword123!"
            }
        )
        assert register_response.status_code == 200
        
        # Step 2: Login
        login_response = client.post(
            "/auth/login",
            json={
                "email": "e2euser@example.com",
                "password": "E2EPassword123!"
            }
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # Step 3: Create Run
        run_response = client.post(
            "/runs",
            headers=headers,
            json={
                "prompt": "Optimize aspirin to maximize QED",
                "max_iterations": 5,
                "batch_size": 3
            }
        )
        assert run_response.status_code == 200
        run_id = run_response.json()["run_id"]
        
        # Step 4: Verify run exists
        get_run_response = client.get(f"/runs/{run_id}", headers=headers)
        assert get_run_response.status_code == 200
        assert get_run_response.json()["run_id"] == run_id


@pytest.mark.e2e
class TestConversationalWorkflow:
    """Test conversational UI workflow."""
    
    def test_conversation_to_run_creation(self, client, auth_headers):
        """Test: Start Conversation → Complete Flow → Create Run."""
        # Step 1: Start conversation
        start_response = client.post(
            "/conversations",
            headers=auth_headers,
            json={"initial_message": "I want to optimize aspirin"}
        )
        assert start_response.status_code == 200
        conv_id = start_response.json()["conversation_id"]
        
        # Step 2: Provide optimization targets
        targets_response = client.post(
            f"/conversations/{conv_id}/message",
            headers=auth_headers,
            json={"message": "maximize QED and minimize logP"}
        )
        assert targets_response.status_code == 200
        
        # Step 3: Provide parameters
        params_response = client.post(
            f"/conversations/{conv_id}/message",
            headers=auth_headers,
            json={"message": "10 iterations, 5 per batch"}
        )
        assert params_response.status_code == 200
        
        # Should be ready for confirmation
        state = params_response.json()["state"]
        assert state in ["confirmation", "collecting_parameters"]
        
        # If we implemented run creation from conversation:
        # Step 4: Confirm and create run
        # confirm_response = client.post(
        #     f"/conversations/{conv_id}/confirm",
        #     headers=auth_headers,
        #     json={"confirmed": True}
        # )
        # assert confirm_response.status_code == 200
        # assert "run_id" in confirm_response.json()


@pytest.mark.e2e
class TestMultiUserIsolation:
    """Test that users can only access their own data."""
    
    def test_user_data_isolation(self, client):
        """Test that User A cannot access User B's data."""
        # Create User A
        client.post(
            "/auth/register",
            json={
                "email": "usera@example.com",
                "username": "usera",
                "password": "PasswordA123!"
            }
        )
        login_a = client.post(
            "/auth/login",
            json={"email": "usera@example.com", "password": "PasswordA123!"}
        )
        token_a = login_a.json()["access_token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        
        # Create User B
        client.post(
            "/auth/register",
            json={
                "email": "userb@example.com",
                "username": "userb",
                "password": "PasswordB123!"
            }
        )
        login_b = client.post(
            "/auth/login",
            json={"email": "userb@example.com", "password": "PasswordB123!"}
        )
        token_b = login_b.json()["access_token"]
        headers_b = {"Authorization": f"Bearer {token_b}"}
        
        # User A creates a run
        run_a_response = client.post(
            "/runs",
            headers=headers_a,
            json={
                "prompt": "User A's run",
                "max_iterations": 5,
                "batch_size": 3
            }
        )
        run_a_id = run_a_response.json()["run_id"]
        
        # User B tries to access User A's run
        access_response = client.get(f"/runs/{run_a_id}", headers=headers_b)
        
        # Should be forbidden or not found
        assert access_response.status_code in [403, 404]
        
        # User B's runs list should not include User A's run
        list_response = client.get("/runs", headers=headers_b)
        assert list_response.status_code == 200
        user_b_runs = list_response.json()
        assert not any(r["run_id"] == run_a_id for r in user_b_runs)


@pytest.mark.e2e
@pytest.mark.slow
class TestSessionManagement:
    """Test session management across multiple devices."""
    
    def test_logout_all_devices(self, client, test_user, test_user_data):
        """Test logout from all devices invalidates all sessions."""
        # Login from "device 1"
        login1 = client.post(
            "/auth/login",
            json={
                "email": test_user_data["email"],
                "password": test_user_data["password"]
            }
        )
        token1 = login1.json()["access_token"]
        headers1 = {"Authorization": f"Bearer {token1}"}
        
        # Login from "device 2"
        login2 = client.post(
            "/auth/login",
            json={
                "email": test_user_data["email"],
                "password": test_user_data["password"]
            }
        )
        token2 = login2.json()["access_token"]
        headers2 = {"Authorization": f"Bearer {token2}"}
        
        # Both sessions should work
        assert client.get("/auth/me", headers=headers1).status_code == 200
        assert client.get("/auth/me", headers=headers2).status_code == 200
        
        # Logout from all devices using device 1
        client.post("/auth/logout-all", headers=headers1)
        
        # Both sessions should now be invalid
        assert client.get("/auth/me", headers=headers1).status_code == 401
        assert client.get("/auth/me", headers=headers2).status_code == 401


@pytest.mark.e2e
class TestErrorRecovery:
    """Test error handling and recovery."""
    
    def test_invalid_run_parameters(self, client, auth_headers):
        """Test graceful handling of invalid run parameters."""
        response = client.post(
            "/runs",
            headers=auth_headers,
            json={
                "prompt": "Test",
                "max_iterations": -1,  # Invalid
                "batch_size": 5
            }
        )
        
        # Should return validation error
        assert response.status_code == 422
    
    def test_malformed_request_body(self, client, auth_headers):
        """Test handling of malformed request body."""
        response = client.post(
            "/conversations",
            headers=auth_headers,
            data="not-valid-json"  # Malformed JSON
        )
        
        # Should return 422 (validation error)
        assert response.status_code == 422
