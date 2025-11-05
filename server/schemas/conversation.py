"""Pydantic schemas for conversational UI endpoints."""

from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict

class ConversationState(str, Enum):
    """Conversation states in the optimization flow."""
    GREETING = "greeting"
    COLLECTING_MOLECULE = "collecting_molecule"
    COLLECTING_TARGETS = "collecting_targets"
    COLLECTING_PARAMETERS = "collecting_parameters"
    CONFIRMATION = "confirmation"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class OptimizationMode(str, Enum):
    """Optimization modes for target properties."""
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"
    MATCH = "match"


class TargetProperty(BaseModel):
    """Target property for optimization."""
    name: str = Field(..., description="Property name (e.g., QED, logP, SA_Score)")
    mode: OptimizationMode = Field(..., description="Optimization mode")
    target_value: Optional[float] = Field(None, description="Target value for match mode")
    weight: float = Field(1.0, ge=0, description="Property weight/priority")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "name": "QED",
                "mode": "maximize",
                "weight": 1.0
            }
        }
    )

class ConversationContext(BaseModel):
    """Collected context during conversation."""

    # Required information
    starting_molecule: Optional[str] = Field(None, description="Starting molecule SMILES")
    molecule_source: Optional[str] = Field(None, description="Source: 'smiles', 'name', 'enumeration'")
    molecule_name: Optional[str] = Field(None, description="Molecule name if provided")

    # Target properties
    targets: List[TargetProperty] = Field(default_factory=list, description="Optimization targets")

    # Parameters
    max_iterations: Optional[int] = Field(None, ge=1, le=100, description="Maximum iterations")
    batch_size: Optional[int] = Field(None, ge=1, le=50, description="Molecules per batch")

    # Optional information
    notes: Optional[str] = Field(None, description="User notes")
    protein_target: Optional[Dict[str, Any]] = Field(None, description="Protein target information")

    # Tracking
    needs_clarification: List[str] = Field(default_factory=list, description="Items needing clarification")
    clarifications_asked: List[str] = Field(default_factory=list, description="Already asked clarifications")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "starting_molecule": "CC(=O)Oc1ccccc1C(=O)O",
                "molecule_source": "smiles",
                "molecule_name": "aspirin",
                "targets": [
                    {"name": "QED", "mode": "maximize", "weight": 1.0},
                    {"name": "logP", "mode": "match", "target_value": 2.5, "weight": 0.5}
                ],
                "max_iterations": 10,
                "batch_size": 5,
                "notes": "Exploring aspirin analogs"
            }
        }
    )

class ConversationStartRequest(BaseModel):
    """Request to start a new conversation."""
    initial_message: Optional[str] = Field(None, description="Optional initial message from user")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "initial_message": "I want to optimize aspirin for better drug-likeness"
            }
        }
    )

class ConversationMessageRequest(BaseModel):
    """Request to send a message in conversation."""
    message: str = Field(..., min_length=1, description="User message")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "message": "I want to maximize QED and keep logP around 2.5"
            }
        }
    )

class ConversationResponse(BaseModel):
    """Response from conversation system."""
    conversation_id: str = Field(..., description="Conversation UUID")
    state: ConversationState = Field(..., description="Current conversation state")
    message: str = Field(..., description="System response message")
    context: ConversationContext = Field(..., description="Current collected context")
    suggestions: List[str] = Field(default_factory=list, description="Suggested responses")
    can_proceed: bool = Field(False, description="Whether run can be started")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
                "state": "collecting_targets",
                "message": "Great! I'll help you optimize aspirin. What properties would you like to optimize?",
                "context": {
                    "starting_molecule": "CC(=O)Oc1ccccc1C(=O)O",
                    "molecule_source": "name",
                    "molecule_name": "aspirin"
                },
                "suggestions": [
                    "Maximize QED",
                    "Minimize logP",
                    "Improve synthetic accessibility"
                ],
                "can_proceed": False
            }
        }
    )

class ConversationConfirmRequest(BaseModel):
    """Request to confirm and create run."""
    confirmed: bool = Field(..., description="Whether user confirms the configuration")
    changes: Optional[str] = Field(None, description="Requested changes if not confirmed")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "confirmed": True
            }
        }
    )

class ConversationCreateRunResponse(BaseModel):
    """Response after creating run from conversation."""
    run_id: str = Field(..., description="Created run ID")
    message: str = Field(..., description="Success message")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "run_id": "run_abc123",
                "message": "Optimization started! You can track progress at /runs/run_abc123"
            }
        }
    )

class ConversationListResponse(BaseModel):
    """Response for listing conversations."""
    conversations: List[Dict[str, Any]] = Field(..., description="List of conversations")
    total: int = Field(..., description="Total conversation count")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "conversations": [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "state": "completed",
                        "created_at": "2025-01-15T10:30:00Z",
                        "updated_at": "2025-01-15T10:35:00Z",
                        "run_id": "run_abc123"
                    }
                ],
                "total": 1
            }
        }
    )