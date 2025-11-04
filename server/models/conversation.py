"""Conversation models for interactive dialogue-based optimization setup."""

from datetime import datetime, timezone
from typing import Optional
import uuid

from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from server.database import Base


class Conversation(Base):
    """Conversation model for multi-turn dialogue."""

    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)

    status = Column(String(50), nullable=False, default="active", index=True)
    # Status values: active, completed, abandoned

    context = Column(JSON, nullable=False, default=dict)
    # Stores collected information:
    # {
    #   "starting_molecule": str,
    #   "molecule_source": str,
    #   "targets": [{"name": str, "mode": str, "weight": float}],
    #   "max_iterations": int,
    #   "batch_size": int,
    #   "notes": str,
    #   "needs_clarification": [str],
    #   "current_state": str  # conversation state
    # }

    created_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="conversations")
    session = relationship("Session", back_populates="conversations")
    messages = relationship("ConversationMessage", back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationMessage.created_at")

    __table_args__ = (
        Index('idx_conversations_user_id', 'user_id'),
        Index('idx_conversations_status', 'status'),
    )

    # Property to provide backward compatibility with 'state' name used in API
    @property
    def state(self):
        """Alias for status to match API schema."""
        return self.status
    
    @state.setter
    def state(self, value):
        """Set status through state property."""
        self.status = value

    def __repr__(self):
        return f"<Conversation(id={self.id}, user_id={self.user_id}, status={self.status})>"

    def to_dict(self, include_messages: bool = False):
        """Convert to dictionary for API responses."""
        data = {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "session_id": str(self.session_id),
            "status": self.status,
            "context": self.context,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

        if include_messages:
            data["messages"] = [msg.to_dict() for msg in self.messages]

        return data


class ConversationMessage(Base):
    """Individual messages in a conversation."""

    __tablename__ = "conversation_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)

    role = Column(String(20), nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)

    extra_metadata = Column(JSON, nullable=False, default=dict)
    # Can store: extracted_info, confidence, intent, etc.

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        Index('idx_conv_messages_conversation_id', 'conversation_id'),
        Index('idx_conv_messages_created_at', 'created_at'),
    )

    def __repr__(self):
        return f"<ConversationMessage(id={self.id}, conversation_id={self.conversation_id}, role={self.role})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "conversation_id": str(self.conversation_id),
            "role": self.role,
            "content": self.content,
            "metadata": self.extra_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
