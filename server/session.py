"""Session management for multi-user LIZARD instances.

Handles user sessions, authentication, and session isolation to support
multiple concurrent users running experiments.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from uuid import uuid4
from pydantic import BaseModel, Field
import secrets


class Session(BaseModel):
    """Represents an active user session."""
    id: str = Field(default_factory=lambda: f"session_{uuid4().hex}")
    user_id: str
    username: str
    email: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    last_activity: datetime = Field(default_factory=datetime.now)
    expires_at: datetime = Field(default_factory=lambda: datetime.now() + timedelta(hours=24))
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    
    def is_expired(self) -> bool:
        """Check if the session has expired."""
        return datetime.now() > self.expires_at
    
    def refresh(self):
        """Update last activity and extend expiration."""
        self.last_activity = datetime.now()
        self.expires_at = datetime.now() + timedelta(hours=24)


class SessionToken(BaseModel):
    """Authentication token for a session."""
    token: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    session_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    expires_at: datetime = Field(default_factory=lambda: datetime.now() + timedelta(hours=24))


class SessionManager:
    """Manages user sessions and authentication tokens."""
    
    def __init__(self):
        self._sessions: Dict[str, Session] = {}
        self._tokens: Dict[str, SessionToken] = {}
        self._user_sessions: Dict[str, list[str]] = {}  # user_id -> [session_ids]
    
    def create_session(
        self,
        user_id: str,
        username: str,
        email: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> tuple[Session, SessionToken]:
        """Create a new session for a user."""
        session = Session(
            user_id=user_id,
            username=username,
            email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=metadata or {}
        )
        
        token = SessionToken(session_id=session.id)
        
        self._sessions[session.id] = session
        self._tokens[token.token] = token
        
        if user_id not in self._user_sessions:
            self._user_sessions[user_id] = []
        self._user_sessions[user_id].append(session.id)
        
        return session, token
    
    def get_session_by_token(self, token: str) -> Optional[Session]:
        """Retrieve a session using its authentication token."""
        token_obj = self._tokens.get(token)
        if not token_obj:
            return None
        
        if datetime.now() > token_obj.expires_at:
            self._tokens.pop(token, None)
            return None
        
        session = self._sessions.get(token_obj.session_id)
        if not session:
            return None
        
        if session.is_expired() or not session.is_active:
            return None
        
        session.refresh()
        return session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieve a session by ID."""
        session = self._sessions.get(session_id)
        if session and not session.is_expired() and session.is_active:
            return session
        return None
    
    def invalidate_session(self, session_id: str) -> bool:
        """Invalidate a session."""
        session = self._sessions.get(session_id)
        if session:
            session.is_active = False
            # Remove all tokens for this session
            tokens_to_remove = [
                token for token, token_obj in self._tokens.items()
                if token_obj.session_id == session_id
            ]
            for token in tokens_to_remove:
                self._tokens.pop(token)
            return True
        return False
    
    def get_user_sessions(self, user_id: str) -> list[Session]:
        """Get all active sessions for a user."""
        session_ids = self._user_sessions.get(user_id, [])
        return [
            session for session in [self._sessions.get(sid) for sid in session_ids]
            if session and not session.is_expired() and session.is_active
        ]
    
    def cleanup_expired_sessions(self):
        """Remove expired sessions and tokens."""
        # Clean up expired sessions
        expired_sessions = [
            sid for sid, session in self._sessions.items()
            if session.is_expired()
        ]
        for sid in expired_sessions:
            self._sessions.pop(sid)
        
        # Clean up expired tokens
        expired_tokens = [
            token for token, token_obj in self._tokens.items()
            if datetime.now() > token_obj.expires_at
        ]
        for token in expired_tokens:
            self._tokens.pop(token)
    
    def get_all_active_sessions(self) -> list[Session]:
        """Get all currently active sessions."""
        return [
            session for session in self._sessions.values()
            if not session.is_expired() and session.is_active
        ]


# Global session manager instance
session_manager = SessionManager()
