"""Run-related Pydantic schemas."""

from datetime import datetime
from typing import Literal, Optional, List, Dict, TypeAlias
from uuid import UUID
from pydantic import BaseModel, Field, computed_field, model_validator


BoltzExecutionPreference: TypeAlias = Literal["auto", "prediction", "library_screen"]
BoltzMetric: TypeAlias = Literal[
    "binding_affinity",
    "boltz_binding_confidence",
    "boltz_optimization_score",
    "boltz_structure_confidence",
]


class BoltzRunConfiguration(BaseModel):
    provider: Literal["self_hosted", "platform"] = "self_hosted"
    credential_id: Optional[UUID] = None
    execution_preference: BoltzExecutionPreference = "auto"
    metrics: List[BoltzMetric] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_provider(self) -> "BoltzRunConfiguration":
        if self.provider == "platform" and self.credential_id is None:
            raise ValueError("credential_id is required for Boltz Platform")
        if self.provider == "self_hosted" and self.credential_id is not None:
            raise ValueError("credential_id is only valid for Boltz Platform")
        if self.provider == "platform" and "binding_affinity" in self.metrics:
            raise ValueError("Boltz Platform does not produce binding_affinity")
        return self


class CharacterizationRunConfiguration(BaseModel):
    boltz: BoltzRunConfiguration = Field(default_factory=BoltzRunConfiguration)


class RunCreateRequest(BaseModel):
    """Request schema for creating a new run."""
    prompt: str = Field(..., description="User optimization prompt")
    max_iterations: Optional[int] = Field(None, ge=1)
    batch_size: Optional[int] = Field(None, ge=1)
    note: Optional[str] = None
    characterization: Optional[CharacterizationRunConfiguration] = None


class RunInfo(BaseModel):
    """Response schema for run information."""
    id: str
    status: str
    created_at: datetime
    updated_at: datetime
    prompt: Optional[str] = Field(default=None, description="Original optimization prompt provided by the user.")
    exit_reason: Optional[str] = None
    summary_available: bool = False
    results_available: bool = False
    paths: Dict[str, str] = Field(default_factory=dict)
    note: Optional[str] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    session_id: Optional[str] = None
    starting_molecules: List[str] = Field(
        default_factory=list,
        description="Initial list of molecules (SMILES) provided to the workflow."
    )

    @computed_field(return_type=str, alias="run_id")
    def computed_run_id(self) -> str:
        """Provide backward-compatible `run_id` field in serialized output."""
        return self.id


class RunList(BaseModel):
    """Response schema for list of runs."""
    runs: List[RunInfo]
