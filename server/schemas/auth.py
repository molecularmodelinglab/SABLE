"""Pydantic schemas for authentication endpoints."""

from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# Simple legacy schemas for backward compatibility
class LoginRequest(BaseModel):
    """Simple login request (legacy)."""
    username: str
    email: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class LoginResponse(BaseModel):
    """Simple login response (legacy)."""
    session_id: str
    token: str
    user_id: str
    username: str
    expires_at: datetime


class UserRegisterRequest(BaseModel):
    """Request schema for user registration."""

    email: EmailStr = Field(..., description="User email address")
    username: str = Field(..., min_length=3, max_length=50, description="Username")
    password: str = Field(..., description="Password")

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        """Validate username is alphanumeric with underscores."""
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username must contain only letters, numbers, underscores, and hyphens")
        return v

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "username": "johndoe",
                "password": "SecurePassword123!"
            }
        }
    )

class UserLoginRequest(BaseModel):
    """Request schema for user login."""

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., description="Password")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "SecurePassword123!"
            }
        }
    )

class UserResponse(BaseModel):
    """Response schema for user information."""

    id: UUID = Field(..., description="User UUID")
    email: str = Field(..., description="User email address")
    username: str = Field(..., description="Username")
    auth_provider: str = Field(..., description="Authentication provider (local, auth0)")
    is_active: bool = Field(..., description="Whether account is active")
    is_verified: bool = Field(..., description="Whether email is verified")
    created_at: datetime = Field(..., description="Account creation timestamp")
    last_login: Optional[datetime] = Field(None, description="Last login timestamp")

    model_config = ConfigDict(
        from_attributes = True,
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "user@example.com",
                "username": "johndoe",
                "auth_provider": "local",
                "is_active": True,
                "is_verified": True,
                "created_at": "2025-01-01T12:00:00Z",
                "last_login": "2025-01-15T08:30:00Z"
            }
        }
    )

class SessionResponse(BaseModel):
    """Response schema for session information."""

    id: UUID = Field(..., description="Session UUID")
    user_id: UUID = Field(..., description="User UUID")
    created_at: datetime = Field(..., description="Session creation timestamp")
    last_activity: datetime = Field(..., description="Last activity timestamp")
    expires_at: datetime = Field(..., description="Session expiration timestamp")
    ip_address: Optional[str] = Field(None, description="Client IP address")

    model_config = ConfigDict(
        from_attributes = True
    )

class UserLoginResponse(BaseModel):
    """Response schema for successful login."""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type")
    user: UserResponse = Field(..., description="User information")
    session: SessionResponse = Field(..., description="Session information")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "user": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "email": "user@example.com",
                    "username": "johndoe",
                    "auth_provider": "local",
                    "is_active": True,
                    "is_verified": True,
                    "created_at": "2025-01-01T12:00:00Z",
                    "last_login": "2025-01-15T08:30:00Z"
                },
                "session": {
                    "id": "650e8400-e29b-41d4-a716-446655440001",
                    "user_id": "550e8400-e29b-41d4-a716-446655440000",
                    "created_at": "2025-01-15T08:30:00Z",
                    "last_activity": "2025-01-15T08:30:00Z",
                    "expires_at": "2025-01-16T08:30:00Z",
                    "ip_address": "192.168.1.100"
                }
            }
        }
    )

class PasswordChangeRequest(BaseModel):
    """Request schema for password change."""

    current_password: str = Field(..., description="Current password")
    new_password: str = Field(..., description="New password")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "current_password": "OldPassword123!",
                "new_password": "NewSecurePassword456!"
            }
        }
    )

class EmailVerificationRequest(BaseModel):
    """Request schema for email verification."""

    token: str = Field(..., description="Email verification token")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
            }
        }
    )

class PasswordResetRequest(BaseModel):
    """Request schema for password reset initiation."""

    email: EmailStr = Field(..., description="User email address")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "email": "user@example.com"
            }
        }
    )

class PasswordResetConfirm(BaseModel):
    """Request schema for password reset confirmation."""

    token: str = Field(..., description="Password reset token")
    new_password: str = Field(..., min_length=8, description="New password")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "new_password": "NewSecurePassword123!"
            }
        }
    )

class MessageResponse(BaseModel):
    """Generic message response."""

    message: str = Field(..., description="Response message")
    success: bool = Field(default=True, description="Whether operation was successful")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "message": "Operation completed successfully",
                "success": True
            }
        }
    )

class ErrorResponse(BaseModel):
    """Error response schema."""

    detail: str = Field(..., description="Error details")
    code: Optional[str] = Field(None, description="Error code")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "detail": "Invalid credentials",
                "code": "AUTH_FAILED"
            }
        }
    )

class SessionListResponse(BaseModel):
    """Response schema for listing sessions."""

    sessions: list[SessionResponse] = Field(..., description="List of user sessions")
    total: int = Field(..., description="Total number of sessions")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "sessions": [
                    {
                        "id": "650e8400-e29b-41d4-a716-446655440001",
                        "user_id": "550e8400-e29b-41d4-a716-446655440000",
                        "created_at": "2025-01-15T08:30:00Z",
                        "last_activity": "2025-01-15T10:30:00Z",
                        "expires_at": "2025-01-16T08:30:00Z",
                        "ip_address": "192.168.1.100"
                    }
                ],
                "total": 1
            }
        }
    )