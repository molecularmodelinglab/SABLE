from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProviderJobResponse(BaseModel):
    id: UUID
    provider: str
    execution_kind: str
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    submitted_at: datetime
    last_polled_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)