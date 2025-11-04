"""
Integration tests for conversation API endpoints.
"""
import pytest


@pytest.mark.integration
@pytest.mark.api
class TestConversationEndpoints:
    """Tests for conversation API endpoints."""
    
    def test_start_conversation(self, client, auth_headers):
        """Test starting a new conversation."""
        response = client.post(
            "/conversations",
            headers=auth_headers,
            json={"initial_message": "I want to optimize aspirin"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "conversation_id" in data
        assert "state" in data
        assert "message" in data
        assert "context" in data
    
    def test_start_conversation_no_auth(self, client):
        """Test starting conversation without authentication."""
        response = client.post(
            "/conversations",
            json={"initial_message": "I want to optimize aspirin"}
        )
        
        assert response.status_code == 401
    
    def test_list_conversations(self, client, auth_headers, test_conversation):
        """Test listing user's conversations."""
        response = client.get("/conversations", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(c["id"] == str(test_conversation.id) for c in data)
    
    def test_get_conversation(self, client, auth_headers, test_conversation):
        """Test getting a specific conversation."""
        response = client.get(
            f"/conversations/{test_conversation.id}",
            headers=auth_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(test_conversation.id)
        assert "state" in data
        assert "context" in data
        assert "messages" in data
    
    def test_get_conversation_unauthorized(
        self, client, auth_headers, test_conversation, another_user, db_session
    ):
        """Test accessing another user's conversation."""
        # Create conversation for another user
        from server.models.conversation import Conversation
        from server.models.session import Session as SessionModel
        from datetime import datetime, timedelta, timezone
        
        # Create a session for the other user
        other_session = SessionModel(
            user_id=another_user.id,
            token="other-user-session-token",
            ip_address=None,
            user_agent="test-client",
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            is_active=True
        )
        db_session.add(other_session)
        db_session.flush()
        
        other_conv = Conversation(
            user_id=another_user.id,
            session_id=other_session.id,
            status="active",
            context={}
        )
        db_session.add(other_conv)
        db_session.commit()
        db_session.refresh(other_conv)
        other_conv_id = other_conv.id
        
        # Try to access with test_user's token
        response = client.get(
            f"/conversations/{other_conv_id}",
            headers=auth_headers
        )
        
        assert response.status_code == 404
    
    def test_send_message(self, client, auth_headers, test_conversation):
        """Test sending a message in conversation."""
        response = client.post(
            f"/conversations/{test_conversation.id}/message",
            headers=auth_headers,
            json={"message": "I want to optimize aspirin"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "state" in data
        assert "message" in data
        assert "context" in data
    
    def test_abandon_conversation(self, client, auth_headers, test_conversation):
        """Test abandoning a conversation."""
        response = client.delete(
            f"/conversations/{test_conversation.id}",
            headers=auth_headers
        )
        
        assert response.status_code == 200
        
        # Verify conversation is marked as abandoned
        response = client.get(
            f"/conversations/{test_conversation.id}",
            headers=auth_headers
        )
        
        # Should still exist but be in abandoned state
        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "abandoned"


@pytest.mark.integration
@pytest.mark.api
class TestConversationFlow:
    """Tests for complete conversation flows."""
    
    def test_complete_conversation_flow(
        self, client, auth_headers, sample_conversation_context
    ):
        """Test a complete conversation from start to run creation."""
        # Start conversation
        response = client.post(
            "/conversations",
            headers=auth_headers,
            json={"initial_message": "I want to optimize aspirin"}
        )
        assert response.status_code == 200
        conv_id = response.json()["conversation_id"]
        
        # Provide targets
        response = client.post(
            f"/conversations/{conv_id}/message",
            headers=auth_headers,
            json={"message": "maximize QED and keep logP around 2.5"}
        )
        assert response.status_code == 200
        
        # Provide parameters
        response = client.post(
            f"/conversations/{conv_id}/message",
            headers=auth_headers,
            json={"message": "use 10 iterations with 5 molecules per batch"}
        )
        assert response.status_code == 200
        
        # Should now be in confirmation state
        data = response.json()
        assert data["state"] == "confirmation" or data["can_proceed"] is True
