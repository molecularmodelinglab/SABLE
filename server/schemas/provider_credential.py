from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ProviderCredentialCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: Literal["boltz_platform"] = "boltz_platform"
    name: str = Field(min_length=1, max_length=100)
    api_key: SecretStr = Field(min_length=1)
    validate_credential: bool = Field(default=True, alias="validate")


class ProviderCredentialUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    api_key: Optional[SecretStr] = Field(default=None, min_length=1)
    validate_credential: bool = Field(default=True, alias="validate")

    @model_validator(mode="after")
    def require_change(self) -> "ProviderCredentialUpdate":
        if self.name is None and self.api_key is None:
            raise ValueError("Provide a name or api_key to update.")
        return self


class ProviderCredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    name: str
    key_hint: str
    status: str
    last_validated_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime