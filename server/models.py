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


class RunList(BaseModel):
    runs: List[RunInfo]
