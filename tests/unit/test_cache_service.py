"""
Unit tests for cache service.
"""
import pytest
from server.services.cache_service import cache_service


@pytest.mark.unit
@pytest.mark.cache
class TestCacheService:
    """Tests for Redis cache service."""
    
    def test_is_connected(self):
        """Test Redis connection check."""
        # Should be connected in test environment
        assert cache_service.is_connected() is True
    
    def test_set_and_get(self):
        """Test basic set and get operations."""
        key = "test_key"
        value = {"data": "test_value"}
        
        cache_service.set(key, value, ttl_seconds=60)
        retrieved = cache_service.get(key)
        
        assert retrieved == value
    
    def test_get_nonexistent_key(self):
        """Test getting non-existent key."""
        result = cache_service.get("nonexistent_key")
        
        assert result is None
    
    def test_delete(self):
        """Test delete operation."""
        key = "test_delete"
        cache_service.set(key, "value", ttl_seconds=60)
        
        cache_service.delete(key)
        retrieved = cache_service.get(key)
        
        assert retrieved is None
    
    def test_cache_user(self, test_user):
        """Test user caching."""
        cache_service.cache_user(test_user)
        
        cached = cache_service.get_cached_user(str(test_user.id))
        
        assert cached is not None
        assert cached["id"] == str(test_user.id)
        assert cached["email"] == test_user.email
        assert cached["username"] == test_user.username
    
    def test_invalidate_user(self, test_user):
        """Test user cache invalidation."""
        cache_service.cache_user(test_user)
        cache_service.invalidate_user(str(test_user.id))
        
        cached = cache_service.get_cached_user(str(test_user.id))
        
        assert cached is None
    
    def test_cache_run_status(self):
        """Test run status caching."""
        run_id = "test-run-123"
        status_data = {
            "status": "running",
            "progress": 50,
            "iteration": 5
        }
        
        cache_service.cache_run_status(run_id, status_data)
        cached = cache_service.get_cached_run_status(run_id)
        
        assert cached == status_data
    
    def test_check_rate_limit(self):
        """Test rate limiting."""
        key = "test_rate_limit"
        max_attempts = 3
        window_seconds = 60
        
        # First 3 attempts should succeed
        for i in range(max_attempts):
            allowed, remaining = cache_service.check_rate_limit(
                key, max_attempts, window_seconds
            )
            assert allowed is True
            assert remaining == max_attempts - i - 1
        
        # 4th attempt should fail
        allowed, remaining = cache_service.check_rate_limit(
            key, max_attempts, window_seconds
        )
        assert allowed is False
        assert remaining == 0
    
    def test_reset_rate_limit(self):
        """Test rate limit reset."""
        key = "test_rate_reset"
        max_attempts = 2
        
        # Exhaust rate limit
        for _ in range(max_attempts):
            cache_service.check_rate_limit(key, max_attempts, 60)
        
        # Reset
        cache_service.reset_rate_limit(key)
        
        # Should be allowed again
        allowed, remaining = cache_service.check_rate_limit(key, max_attempts, 60)
        assert allowed is True
    
    def test_delete_pattern(self):
        """Test pattern-based deletion."""
        # Set multiple keys with pattern
        cache_service.set("user:1:data", "value1", 60)
        cache_service.set("user:2:data", "value2", 60)
        cache_service.set("other:data", "value3", 60)
        
        # Delete all user:* keys
        cache_service.delete_pattern("user:*")
        
        # User keys should be deleted
        assert cache_service.get("user:1:data") is None
        assert cache_service.get("user:2:data") is None
        
        # Other key should remain
        assert cache_service.get("other:data") == "value3"
    
    def test_ttl_expiration(self):
        """Test that keys expire after TTL."""
        import time
        
        key = "test_ttl"
        cache_service.set(key, "value", ttl_seconds=1)
        
        # Should exist immediately
        assert cache_service.get(key) == "value"
        
        # Wait for expiration
        time.sleep(2)
        
        # Should be expired
        assert cache_service.get(key) is None
