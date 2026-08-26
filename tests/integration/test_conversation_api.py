"""
Integration tests for conversation API endpoints.
"""
import pytest

from server.models.run import Run


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
        
        assert response.status_code in (401, 403)
    
    def test_list_conversations(self, client, auth_headers, test_conversation):
        """Test listing user's conversations."""
        response = client.get("/conversations", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "conversations" in data
        assert "total" in data
        assert data["total"] >= 1
        assert any(c["id"] == str(test_conversation.id) for c in data["conversations"])
    
    def test_get_conversation(self, client, auth_headers, test_conversation):
        """Test getting a specific conversation."""
        response = client.get(
            f"/conversations/{test_conversation.id}",
            headers=auth_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["conversation_id"] == str(test_conversation.id)
        assert "state" in data
        assert "context" in data
    
    def test_get_conversation_unauthorized(
        self, client, auth_headers, test_conversation, another_user, make_conversation
    ):
        """Test accessing another user's conversation."""
        # Create conversation for another user
        other_conv = make_conversation(another_user)
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
        assert data["conversation_id"] == str(test_conversation.id)
    
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

    def test_confirm_without_boltz_access_preserves_client_error(
        self, client, auth_headers, test_user, make_conversation, db_session
    ):
        conversation = make_conversation(
            test_user,
            state="confirmation",
            context={
                "starting_molecule": "CC(=O)Oc1ccccc1C(=O)O",
                "targets": [{"name": "QED", "mode": "maximize", "weight": 1.0}],
                "max_iterations": 10,
                "batch_size": 5,
            },
        )

        response = client.post(
            f"/conversations/{conversation.id}/confirm",
            headers=auth_headers,
            json={"confirmed": True},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Configure a Boltz provider in Account before starting a run"
        assert db_session.query(Run).count() == 0
    
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
