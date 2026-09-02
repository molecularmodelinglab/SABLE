"""
Integration tests for runs API endpoints.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from server.models.provider_job import ProviderJob


@pytest.mark.integration
@pytest.mark.api
class TestRunsEndpoints:
    """Tests for runs API endpoints."""
    
    def test_create_run(self, client, auth_headers, admin_headers, test_user):
        """Test creating a new run."""
        request_response = client.post("/auth/boltz-access-request", headers=auth_headers)
        assert request_response.status_code == 200
        approval_response = client.patch(
            f"/admin/users/{test_user.id}/boltz-access",
            headers=admin_headers,
            json={"status": "approved"},
        )
        assert approval_response.status_code == 200

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
        assert "id" in data
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
        
        assert response.status_code in (401, 403)
    
    def test_list_runs(self, client, auth_headers, test_run):
        """Test listing user's runs from database."""
        response = client.get("/runs", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "runs" in data
        runs = data["runs"]
        assert any(r["id"] == test_run.id for r in runs)
    
    def test_list_runs_pagination(self, client, auth_headers):
        """Test pagination in runs listing."""
        response = client.get(
            "/runs?limit=5&offset=0",
            headers=auth_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "runs" in data
        assert len(data["runs"]) <= 5
    
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
        assert "starting_molecules" in data
    
    def test_get_run_unauthorized(self, client, auth_headers, another_user, make_run):
        """Test accessing another user's run (should fail)."""
        other_run = make_run(
            another_user,
            run_id="other-run-123",
            starting_molecules=["CC(=O)O"],
            extra_metadata={"max_iterations": 10, "batch_size": 5},
        )
        other_run_id = other_run.id
        
        # Try to access with test_user's token
        response = client.get(
            f"/runs/{other_run_id}",
            headers=auth_headers
        )
        
        # Should be forbidden or not found (403 or 404)
        assert response.status_code in [403, 404]

    def test_list_provider_jobs_returns_sanitized_progress(
        self,
        client,
        auth_headers,
        db_session,
        test_run,
        test_user,
    ):
        now = datetime.now(timezone.utc)
        for index, submitted_at in enumerate((now - timedelta(minutes=1), now)):
            db_session.add(ProviderJob(
                run_id=test_run.id,
                user_id=test_user.id,
                provider="boltz_platform",
                execution_kind="library_screen",
                provider_job_id=f"external-{uuid4()}",
                protein_scope_id="secret-scope",
                request_fingerprint=uuid4().hex,
                status="running",
                total_items=10,
                completed_items=index + 1,
                failed_items=0,
                submitted_at=submitted_at,
            ))
        db_session.commit()

        response = client.get(
            f"/runs/{test_run.id}/provider-jobs",
            headers=auth_headers,
        )

        assert response.status_code == 200
        jobs = response.json()
        assert [job["completed_items"] for job in jobs] == [2, 1]
        assert jobs[0]["provider"] == "boltz_platform"
        assert jobs[0]["execution_kind"] == "library_screen"
        assert "provider_job_id" not in jobs[0]
        assert "request_fingerprint" not in jobs[0]
        assert "protein_scope_id" not in jobs[0]
        assert "credential_id" not in jobs[0]
    
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
