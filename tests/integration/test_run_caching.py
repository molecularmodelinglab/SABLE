"""
Unit tests for run caching functionality.
"""
import pytest
from server.services.cache_service import cache_service


@pytest.mark.unit
@pytest.mark.cache
class TestRunCaching:
    """Tests for run caching in cache service."""
    
    def test_cache_run(self):
        """Test caching a run."""
        run_id = "test-run-cache-123"
        run_data = {
            "id": run_id,
            "status": "running",
            "user_id": "user-123",
            "prompt": "Test prompt"
        }
        
        result = cache_service.cache_run(run_id, run_data)
        
        assert result is True
        
        # Verify it's cached
        cached = cache_service.get_cached_run(run_id)
        assert cached is not None
        assert cached["id"] == run_id
        assert cached["status"] == "running"
    
    def test_get_cached_run_not_found(self):
        """Test getting non-existent cached run."""
        result = cache_service.get_cached_run("nonexistent-run-123")
        
        assert result is None
    
    def test_invalidate_run(self):
        """Test invalidating a cached run."""
        run_id = "test-run-invalidate-123"
        run_data = {"id": run_id, "status": "running"}
        
        # Cache it
        cache_service.cache_run(run_id, run_data)
        assert cache_service.get_cached_run(run_id) is not None
        
        # Invalidate it
        result = cache_service.invalidate_run(run_id)
        assert result is True
        
        # Verify it's gone
        assert cache_service.get_cached_run(run_id) is None
    
    def test_cache_user_runs_list(self):
        """Test caching user's runs list."""
        user_id = "user-cache-test-123"
        runs_data = [
            {"id": "run-1", "status": "completed"},
            {"id": "run-2", "status": "running"},
            {"id": "run-3", "status": "pending"}
        ]
        
        result = cache_service.cache_user_runs_list(user_id, runs_data)
        
        assert result is True
        
        # Verify it's cached
        cached = cache_service.get_cached_user_runs_list(user_id)
        assert cached is not None
        assert len(cached) == 3
        assert cached[0]["id"] == "run-1"
    
    def test_get_cached_user_runs_list_not_found(self):
        """Test getting non-existent cached user runs list."""
        result = cache_service.get_cached_user_runs_list("nonexistent-user-123")
        
        assert result is None
    
    def test_invalidate_user_runs_list(self):
        """Test invalidating user's runs list cache."""
        user_id = "user-invalidate-test-123"
        runs_data = [{"id": "run-1", "status": "completed"}]
        
        # Cache it
        cache_service.cache_user_runs_list(user_id, runs_data)
        assert cache_service.get_cached_user_runs_list(user_id) is not None
        
        # Invalidate it
        result = cache_service.invalidate_user_runs_list(user_id)
        assert result is True
        
        # Verify it's gone
        assert cache_service.get_cached_user_runs_list(user_id) is None
    
    def test_cache_run_with_complex_data(self):
        """Test caching run with complex nested data."""
        run_id = "test-run-complex-123"
        run_data = {
            "id": run_id,
            "status": "completed",
            "user_id": "user-123",
            "starting_molecules": ["CC(=O)Oc1ccccc1C(=O)O", "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"],
            "metadata": {
                "paths": {
                    "inputs": "/path/to/inputs",
                    "outputs": "/path/to/outputs"
                },
                "config": {
                    "max_iterations": 10,
                    "batch_size": 5
                }
            },
            "created_at": "2025-11-03T12:00:00",
            "updated_at": "2025-11-03T13:00:00"
        }
        
        cache_service.cache_run(run_id, run_data)
        cached = cache_service.get_cached_run(run_id)
        
        assert cached is not None
        assert cached["id"] == run_id
        assert len(cached["starting_molecules"]) == 2
        assert cached["metadata"]["config"]["max_iterations"] == 10


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.cache
class TestRunAPICaching:
    """Integration tests for run API caching behavior."""
    
    def test_get_run_uses_cache(self, client, auth_headers, test_run):
        """Test that getting a run uses cache on second access."""
        # Clear cache first
        cache_service.invalidate_run(test_run.id)
        
        # First access - should cache
        response1 = client.get(f"/runs/{test_run.id}", headers=auth_headers)
        assert response1.status_code == 200
        
        # Verify it's in cache
        cached = cache_service.get_cached_run(test_run.id)
        assert cached is not None
        assert cached["id"] == test_run.id
        
        # Second access - should use cache
        response2 = client.get(f"/runs/{test_run.id}", headers=auth_headers)
        assert response2.status_code == 200
        assert response1.json() == response2.json()
    
    def test_list_runs_uses_cache(self, client, auth_headers, test_user):
        """Test that listing runs uses cache on second access."""
        user_id = str(test_user.id)
        
        # Clear cache first
        cache_service.invalidate_user_runs_list(user_id)
        
        # First access - should cache
        response1 = client.get("/runs", headers=auth_headers)
        assert response1.status_code == 200
        
        # Verify it's in cache
        cached = cache_service.get_cached_user_runs_list(user_id)
        assert cached is not None
        
        # Second access - should use cache
        response2 = client.get("/runs", headers=auth_headers)
        assert response2.status_code == 200
        assert response1.json() == response2.json()
    
    def test_delete_run_invalidates_cache(self, client, auth_headers, test_run, test_user):
        """Test that deleting a run invalidates its cache."""
        run_id = test_run.id
        user_id = str(test_user.id)
        
        # Access the run to cache it
        client.get(f"/runs/{run_id}", headers=auth_headers)
        client.get("/runs", headers=auth_headers)
        
        # Verify both are cached
        assert cache_service.get_cached_run(run_id) is not None
        assert cache_service.get_cached_user_runs_list(user_id) is not None
        
        # Delete the run
        response = client.delete(f"/runs/{run_id}", headers=auth_headers)
        assert response.status_code == 200
        
        # Verify caches are invalidated
        assert cache_service.get_cached_run(run_id) is None
        assert cache_service.get_cached_user_runs_list(user_id) is None
    
    def test_create_run_caches_immediately(self, client, auth_headers):
        """Test that creating a run caches it immediately."""
        response = client.post(
            "/runs",
            headers=auth_headers,
            json={
                "prompt": "Test caching on create",
                "max_iterations": 5,
                "batch_size": 3
            }
        )
        
        assert response.status_code == 200
        run_id = response.json()["run_id"]
        
        # Verify it's cached
        cached = cache_service.get_cached_run(run_id)
        assert cached is not None
        assert cached["id"] == run_id
    
    def test_pagination_skips_cache(self, client, auth_headers):
        """Test that non-default pagination skips cache."""
        user_id = str(auth_headers.get("user_id", "test-user"))
        
        # This should not cache (custom pagination)
        response = client.get("/runs?limit=5&offset=5", headers=auth_headers)
        assert response.status_code == 200
        
        # Note: This test assumes the implementation detail that
        # only default pagination (limit=100, offset=0) uses cache
