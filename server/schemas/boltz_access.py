from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from server.schemas.run import BoltzExecutionPreference, BoltzMetric


BoltzAccessStatus = Literal["not_requested", "pending", "approved", "denied"]


class BoltzSettingsResponse(BaseModel):
    access_status: BoltzAccessStatus = "not_requested"
    can_use_self_hosted: bool = False
    requested_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    provider: Optional[Literal["self_hosted", "platform"]] = None
    credential_id: Optional[UUID] = None
    execution_preference: BoltzExecutionPreference = "auto"
    metrics: list[BoltzMetric] = Field(default_factory=list)


class BoltzSettingsUpdate(BaseModel):
    provider: Literal["self_hosted", "platform"]
    credential_id: Optional[UUID] = None
    execution_preference: BoltzExecutionPreference = "auto"
    metrics: list[BoltzMetric] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_provider(self) -> "BoltzSettingsUpdate":
        if self.provider == "platform" and self.credential_id is None:
            raise ValueError("credential_id is required for Boltz Platform")
        if self.provider == "self_hosted" and self.credential_id is not None:
            raise ValueError("credential_id is only valid for Boltz Platform")
        if self.provider == "platform" and "binding_affinity" in self.metrics:
            raise ValueError("Boltz Platform does not produce binding_affinity")
        return self


class AdminBoltzUserResponse(BoltzSettingsResponse):
    user_id: UUID
    email: str
    username: str


class AdminBoltzAccessReview(BaseModel):
    status: Literal["approved", "denied"]