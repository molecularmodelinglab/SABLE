"""Redis caching service for performance optimization."""

import os
import json
import hashlib
from typing import Optional, Any, Dict, List
from datetime import timedelta

import redis
from redis import Redis
from redis.exceptions import RedisError


class CacheService:
    """Service for managing Redis cache operations."""

    def __init__(self):
        """Initialize Redis connection."""
        redis_url = os.getenv("REDIS_URL", "redis://:redis_password@localhost:6379/0")
        try:
            self.redis: Redis = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            # Test connection
            self.redis.ping()
            self._connected = True
            print("✓ Redis connection established")
        except RedisError as e:
            print(f"⚠ Redis connection failed: {e}")
            print("  Cache operations will be disabled")
            self._connected = False
            self.redis = None

    def is_connected(self) -> bool:
        """Check if Redis is connected and available."""
        return self._connected and self.redis is not None

    # ==================== Session Caching ====================

    def cache_session(self, token: str, session_data: Dict[str, Any], ttl_hours: int = 24) -> bool:
        """
        Cache session data for fast authentication.

        Args:
            token: Session token
            session_data: Session information to cache
            ttl_hours: Time to live in hours

        Returns:
            True if cached successfully, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            key = f"session:{token}"
            value = json.dumps(session_data, default=str)
            ttl = timedelta(hours=ttl_hours)
            self.redis.setex(key, ttl, value)
            return True
        except RedisError as e:
            print(f"Error caching session: {e}")
            return False

    def get_cached_session(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached session data.

        Args:
            token: Session token

        Returns:
            Session data if found, None otherwise
        """
        if not self.is_connected():
            return None

        try:
            key = f"session:{token}"
            value = self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except (RedisError, json.JSONDecodeError) as e:
            print(f"Error retrieving cached session: {e}")
            return None

    def invalidate_session(self, token: str) -> bool:
        """
        Remove session from cache.

        Args:
            token: Session token

        Returns:
            True if deleted, False otherwise
        """
        if not self.is_connected():
            return False

        try:
            key = f"session:{token}"
            self.redis.delete(key)
            return True
        except RedisError as e:
            print(f"Error invalidating session: {e}")
            return False

    def refresh_session_ttl(self, token: str, ttl_hours: int = 24) -> bool:
        """
        Extend session TTL without modifying data.

        Args:
            token: Session token
            ttl_hours: Time to live in hours

        Returns:
            True if extended successfully
        """
        if not self.is_connected():
            return False

        try:
            key = f"session:{token}"
            ttl = int(timedelta(hours=ttl_hours).total_seconds())
            return bool(self.redis.expire(key, ttl))
        except RedisError as e:
            print(f"Error refreshing session TTL: {e}")
            return False

    # ==================== User Caching ====================

    def cache_user(self, user_id: str, user_data: Dict[str, Any], ttl_minutes: int = 60) -> bool:
        """
        Cache user profile data.

        Args:
            user_id: User UUID
            user_data: User profile information
            ttl_minutes: Time to live in minutes

        Returns:
            True if cached successfully
        """
        if not self.is_connected():
            return False

        try:
            key = f"user:{user_id}"
            value = json.dumps(user_data, default=str)
            ttl = timedelta(minutes=ttl_minutes)
            self.redis.setex(key, ttl, value)
            return True
        except RedisError as e:
            print(f"Error caching user: {e}")
            return False

    def get_cached_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached user data.

        Args:
            user_id: User UUID

        Returns:
            User data if found, None otherwise
        """
        if not self.is_connected():
            return None

        try:
            key = f"user:{user_id}"
            value = self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except (RedisError, json.JSONDecodeError) as e:
            print(f"Error retrieving cached user: {e}")
            return None

    def invalidate_user(self, user_id: str) -> bool:
        """
        Remove user from cache.

        Args:
            user_id: User UUID

        Returns:
            True if deleted
        """
        if not self.is_connected():
            return False

        try:
            key = f"user:{user_id}"
            self.redis.delete(key)
            return True
        except RedisError as e:
            print(f"Error invalidating user: {e}")
            return False

    # ==================== Run Caching ====================

    def cache_run(self, run_id: str, run_data: Dict[str, Any], ttl_minutes: int = 10) -> bool:
        """
        Cache complete run information.

        Args:
            run_id: Run identifier
            run_data: Complete run information
            ttl_minutes: Time to live in minutes

        Returns:
            True if cached successfully
        """
        if not self.is_connected():
            return False

        try:
            key = f"run:{run_id}"
            value = json.dumps(run_data, default=str)
            ttl = timedelta(minutes=ttl_minutes)
            self.redis.setex(key, ttl, value)
            return True
        except RedisError as e:
            print(f"Error caching run: {e}")
            return False

    def get_cached_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached run information.

        Args:
            run_id: Run identifier

        Returns:
            Run data if found, None otherwise
        """
        if not self.is_connected():
            return None

        try:
            key = f"run:{run_id}"
            value = self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except (RedisError, json.JSONDecodeError) as e:
            print(f"Error retrieving cached run: {e}")
            return None

    def invalidate_run(self, run_id: str) -> bool:
        """
        Remove run from cache.

        Args:
            run_id: Run identifier

        Returns:
            True if deleted
        """
        if not self.is_connected():
            return False

        try:
            key = f"run:{run_id}"
            self.redis.delete(key)
            return True
        except RedisError as e:
            print(f"Error invalidating run cache: {e}")
            return False

    def cache_user_runs_list(self, user_id: str, runs_data: List[Dict[str, Any]], ttl_minutes: int = 5) -> bool:
        """
        Cache user's runs list.

        Args:
            user_id: User UUID
            runs_data: List of run data
            ttl_minutes: Time to live in minutes

        Returns:
            True if cached successfully
        """
        if not self.is_connected():
            return False

        try:
            key = f"user:{user_id}:runs"
            value = json.dumps(runs_data, default=str)
            ttl = timedelta(minutes=ttl_minutes)
            self.redis.setex(key, ttl, value)
            return True
        except RedisError as e:
            print(f"Error caching user runs list: {e}")
            return False

    def get_cached_user_runs_list(self, user_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Retrieve cached user runs list.

        Args:
            user_id: User UUID

        Returns:
            List of run data if found, None otherwise
        """
        if not self.is_connected():
            return None

        try:
            key = f"user:{user_id}:runs"
            value = self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except (RedisError, json.JSONDecodeError) as e:
            print(f"Error retrieving cached user runs list: {e}")
            return None

    def invalidate_user_runs_list(self, user_id: str) -> bool:
        """
        Invalidate cached user runs list.

        Args:
            user_id: User UUID

        Returns:
            True if deleted
        """
        if not self.is_connected():
            return False

        try:
            key = f"user:{user_id}:runs"
            self.redis.delete(key)
            return True
        except RedisError as e:
            print(f"Error invalidating user runs list: {e}")
            return False

    # ==================== Run Status Caching ====================

    def cache_run_status(self, run_id: str, status_data: Dict[str, Any], ttl_minutes: int = 5) -> bool:
        """
        Cache run status for quick access.

        Args:
            run_id: Run identifier
            status_data: Run status information
            ttl_minutes: Time to live in minutes

        Returns:
            True if cached successfully
        """
        if not self.is_connected():
            return False

        try:
            key = f"run:status:{run_id}"
            value = json.dumps(status_data, default=str)
            ttl = timedelta(minutes=ttl_minutes)
            self.redis.setex(key, ttl, value)
            return True
        except RedisError as e:
            print(f"Error caching run status: {e}")
            return False

    def get_cached_run_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached run status.

        Args:
            run_id: Run identifier

        Returns:
            Run status if found, None otherwise
        """
        if not self.is_connected():
            return None

        try:
            key = f"run:status:{run_id}"
            value = self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except (RedisError, json.JSONDecodeError) as e:
            print(f"Error retrieving cached run status: {e}")
            return None

    # ==================== Conversation Context Caching ====================

    def cache_conversation(self, conversation_id: str, context_data: Dict[str, Any], ttl_minutes: int = 30) -> bool:
        """
        Cache conversation context for active conversations.

        Args:
            conversation_id: Conversation UUID
            context_data: Conversation context and state
            ttl_minutes: Time to live in minutes

        Returns:
            True if cached successfully
        """
        if not self.is_connected():
            return False

        try:
            key = f"conversation:{conversation_id}"
            value = json.dumps(context_data, default=str)
            ttl = timedelta(minutes=ttl_minutes)
            self.redis.setex(key, ttl, value)
            return True
        except RedisError as e:
            print(f"Error caching conversation: {e}")
            return False

    def get_cached_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached conversation context.

        Args:
            conversation_id: Conversation UUID

        Returns:
            Conversation context if found, None otherwise
        """
        if not self.is_connected():
            return None

        try:
            key = f"conversation:{conversation_id}"
            value = self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except (RedisError, json.JSONDecodeError) as e:
            print(f"Error retrieving cached conversation: {e}")
            return None

    def invalidate_conversation(self, conversation_id: str) -> bool:
        """
        Remove conversation from cache.

        Args:
            conversation_id: Conversation UUID

        Returns:
            True if deleted
        """
        if not self.is_connected():
            return False

        try:
            key = f"conversation:{conversation_id}"
            self.redis.delete(key)
            return True
        except RedisError as e:
            print(f"Error invalidating conversation: {e}")
            return False

    # ==================== Rate Limiting ====================

    def check_rate_limit(
        self,
        identifier: str,
        limit: int,
        window_seconds: int = 60,
        namespace: str = "ratelimit"
    ) -> tuple[bool, int, int]:
        """
        Check if request is within rate limit.

        Args:
            identifier: Unique identifier (user_id, ip_address, etc.)
            limit: Maximum requests allowed in window
            window_seconds: Time window in seconds
            namespace: Rate limit namespace/category

        Returns:
            Tuple of (allowed, current_count, remaining)
        """
        if not self.is_connected():
            return (True, 0, limit)  # Allow if Redis unavailable

        try:
            key = f"{namespace}:{identifier}"

            # Get current count
            current = self.redis.get(key)
            if current is None:
                # First request in window
                self.redis.setex(key, window_seconds, 1)
                return (True, 1, limit - 1)

            count = int(current)
            if count >= limit:
                # Rate limit exceeded
                ttl = self.redis.ttl(key)
                return (False, count, 0)

            # Increment counter
            new_count = self.redis.incr(key)
            return (True, new_count, limit - new_count)

        except RedisError as e:
            print(f"Error checking rate limit: {e}")
            return (True, 0, limit)  # Allow on error

    def reset_rate_limit(self, identifier: str, namespace: str = "ratelimit") -> bool:
        """
        Reset rate limit counter for identifier.

        Args:
            identifier: Unique identifier
            namespace: Rate limit namespace

        Returns:
            True if reset successfully
        """
        if not self.is_connected():
            return False

        try:
            key = f"{namespace}:{identifier}"
            self.redis.delete(key)
            return True
        except RedisError as e:
            print(f"Error resetting rate limit: {e}")
            return False

    # ==================== Pub/Sub for Real-time Updates ====================

    def publish_run_update(self, run_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Publish run update to subscribers.

        Args:
            run_id: Run identifier
            update_data: Update information

        Returns:
            True if published successfully
        """
        if not self.is_connected():
            return False

        try:
            channel = f"run:updates:{run_id}"
            message = json.dumps(update_data, default=str)
            self.redis.publish(channel, message)
            return True
        except RedisError as e:
            print(f"Error publishing run update: {e}")
            return False

    # ==================== Generic Caching ====================

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
        """
        Set a generic cache value.

        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl_seconds: Time to live in seconds (None for no expiration)

        Returns:
            True if set successfully
        """
        if not self.is_connected():
            return False

        try:
            serialized = json.dumps(value, default=str)
            if ttl_seconds:
                self.redis.setex(key, ttl_seconds, serialized)
            else:
                self.redis.set(key, serialized)
            return True
        except RedisError as e:
            print(f"Error setting cache value: {e}")
            return False

    def get(self, key: str) -> Optional[Any]:
        """
        Get a generic cache value.

        Args:
            key: Cache key

        Returns:
            Cached value if found, None otherwise
        """
        if not self.is_connected():
            return None

        try:
            value = self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except (RedisError, json.JSONDecodeError) as e:
            print(f"Error getting cache value: {e}")
            return None

    def delete(self, key: str) -> bool:
        """
        Delete a cache key.

        Args:
            key: Cache key

        Returns:
            True if deleted
        """
        if not self.is_connected():
            return False

        try:
            self.redis.delete(key)
            return True
        except RedisError as e:
            print(f"Error deleting cache key: {e}")
            return False

    def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.

        Args:
            pattern: Key pattern (e.g., "session:*")

        Returns:
            Number of keys deleted
        """
        if not self.is_connected():
            return 0

        try:
            keys = self.redis.keys(pattern)
            if keys:
                return self.redis.delete(*keys)
            return 0
        except RedisError as e:
            print(f"Error deleting pattern: {e}")
            return 0

    def clear_all(self) -> bool:
        """
        Clear all cache (use with caution!).

        Returns:
            True if cleared successfully
        """
        if not self.is_connected():
            return False

        try:
            self.redis.flushdb()
            return True
        except RedisError as e:
            print(f"Error clearing cache: {e}")
            return False


# Global cache service instance
cache_service = CacheService()
