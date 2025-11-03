from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    prompt: str = Field(..., description="User optimization prompt")
    max_iterations: Optional[int] = Field(None, ge=1)
    batch_size: Optional[int] = Field(None, ge=1)
    note: Optional[str] = None


class RunInfo(BaseModel):
    id: str
    status: str
    created_at: datetime
    updated_at: datetime
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


class RunList(BaseModel):
    runs: List[RunInfo]


class LoginRequest(BaseModel):
    username: str
    email: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class LoginResponse(BaseModel):
    session_id: str
    token: str
    user_id: str
    username: str
    expires_at: datetime

