"""Run-related Pydantic schemas."""

from datetime import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, Field, computed_field


class RunCreateRequest(BaseModel):
    """Request schema for creating a new run."""
    prompt: str = Field(..., description="User optimization prompt")
    max_iterations: Optional[int] = Field(None, ge=1)
    batch_size: Optional[int] = Field(None, ge=1)
    note: Optional[str] = None


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
