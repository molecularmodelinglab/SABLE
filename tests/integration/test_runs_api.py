"""
Integration tests for runs API endpoints.
"""
import pytest


@pytest.mark.integration
@pytest.mark.api
class TestRunsEndpoints:
    """Tests for runs API endpoints."""
    
    def test_create_run(self, client, auth_headers):
        """Test creating a new run."""
        response = client.post(
            "/runs",
            headers=auth_headers,
            json={
                "prompt": "Optimize aspirin to maximize QED",
                "max_iterations": 10,
                "batch_size": 5,
                "note": "Test run"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "run_id" in data
        assert data["status"] in ["pending", "running"]
    
    def test_create_run_no_auth(self, client):
        """Test creating run without authentication."""
        response = client.post(
            "/runs",
            json={
                "prompt": "Optimize aspirin",
                "max_iterations": 10,
                "batch_size": 5
            }
        )
        
        assert response.status_code == 401
    
    def test_list_runs(self, client, auth_headers, test_run):
        """Test listing user's runs from database."""
        response = client.get("/runs", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # Should include the test run
        assert any(r["id"] == test_run.id for r in data)
    
    def test_list_runs_pagination(self, client, auth_headers):
        """Test pagination in runs listing."""
        response = client.get(
            "/runs?limit=5&offset=0",
            headers=auth_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 5
    
    def test_get_run(self, client, auth_headers, test_run):
        """Test getting a specific run from database."""
        response = client.get(
            f"/runs/{test_run.id}",
            headers=auth_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_run.id
        assert "status" in data
        assert "prompt" in data
    
    def test_get_run_unauthorized(self, client, auth_headers, another_user, db_session):
        """Test accessing another user's run (should fail)."""
        from server.models.run import Run
        from server.models.session import Session as SessionModel
        from datetime import datetime, timedelta, timezone
        
        # Create a session for the other user
        other_session = SessionModel(
            user_id=another_user.id,
            token="other-user-run-session-token",
            ip_address=None,
            user_agent="test-client",
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            is_active=True
        )
        db_session.add(other_session)
        db_session.flush()
        
        # Create run for another user
        other_run = Run(
            id="other-run-123",
            user_id=another_user.id,
            session_id=other_session.id,
            prompt="Test",
            status="pending",
            starting_molecules=["CC(=O)O"],
            extra_metadata={"max_iterations": 10, "batch_size": 5}
        )
        db_session.add(other_run)
        db_session.commit()
        db_session.refresh(other_run)
        other_run_id = other_run.id
        
        # Try to access with test_user's token
        response = client.get(
            f"/runs/{other_run_id}",
            headers=auth_headers
        )
        
        # Should be forbidden or not found (403 or 404)
        assert response.status_code in [403, 404]
    
    def test_delete_run(self, client, auth_headers, test_run):
        """Test deleting a run from database."""
        response = client.delete(
            f"/runs/{test_run.id}",
            headers=auth_headers
        )
        
        assert response.status_code == 200
        
        # Verify run is deleted from database
        response = client.get(
            f"/runs/{test_run.id}",
            headers=auth_headers
        )
        assert response.status_code == 404
    
    def test_get_run_logs(self, client, auth_headers, test_run):
        """Test getting run logs."""
        response = client.get(
            f"/runs/{test_run.id}/logs",
            headers=auth_headers
        )
        
        # Should return empty list or logs
        assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.slow
class TestRunsStreamingEvents:
    """Tests for run event streaming."""
    
    def test_run_events_stream(self, client, auth_headers, test_run):
        """Test SSE event stream for run updates."""
        # This is a simplified test - full SSE testing requires special handling
        response = client.get(
            f"/runs/{test_run.id}/events",
            headers=auth_headers
        )
        
        # Should accept the connection (may return immediately in tests)
        assert response.status_code in [200, 307]  # 307 for redirects
