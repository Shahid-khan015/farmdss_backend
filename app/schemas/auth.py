# Follows pattern from: app/schemas/tractor.py (Pydantic v2, Field, ConfigDict)
"""Request/response schemas for authentication and user profile APIs."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

# Aligned with DB check constraint `ck_users_role`.
ALLOWED_ROLES: tuple[str, ...] = ("farmer", "operator", "owner", "researcher")
PHONE_E164_PATTERN = re.compile(r"^\+?[1-9]\d{1,14}$")


class RegisterRequest(BaseModel):
    """Payload for registering a new user account."""

    model_config = ConfigDict(str_strip_whitespace=True)

    phone_number: str = Field(
        ...,
        description="Phone number in E.164-style form (optional leading +, 1–15 digits after country code).",
        examples=["+919876543210"],
    )
    password: str = Field(..., min_length=8, description="Plain password; min 8 characters.")
    name: str = Field(..., min_length=2, description="Display name.")
    email: Optional[str] = Field(default=None, description="Optional unique email.")
    role: str = Field(..., description="One of: farmer, operator, owner, researcher.")
    business_name: Optional[str] = None
    gst_number: Optional[str] = None
    license_number: Optional[str] = None
    experience_years: Optional[int] = Field(default=None, ge=0)
    wage_rate_per_hour: Optional[Decimal] = Field(default=None, ge=0)
    wage_rate_per_hectare: Optional[Decimal] = Field(default=None, ge=0)
    farm_name: Optional[str] = None
    farm_location: Optional[str] = None
    total_land_hectares: Optional[Decimal] = Field(default=None, ge=0)

    @field_validator("phone_number")
    @classmethod
    def validate_phone_e164(cls, v: str) -> str:
        if not PHONE_E164_PATTERN.match(v.strip()):
            raise ValueError("Invalid phone format; expected E.164-style pattern.")
        return v.strip()

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in ALLOWED_ROLES:
            raise ValueError(f"role must be one of: {', '.join(ALLOWED_ROLES)}")
        return normalized


class LoginRequest(BaseModel):
    """Credentials for password-based login."""

    model_config = ConfigDict(str_strip_whitespace=True)

    phone_number: str = Field(..., description="Registered phone number.")
    password: str = Field(..., description="Plain password.")


class RefreshTokenRequest(BaseModel):
    """Payload to obtain a new access token using a refresh token."""

    refresh_token: str = Field(..., min_length=1, description="Issued refresh token.")


class ProfileUpdateRequest(BaseModel):
    """
    Partial profile update. Fields that are ``None`` are not updated (PATCH semantics).

    Accepts any role-specific columns; the server may ignore fields not applicable to the user's role.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    farm_name: Optional[str] = None
    farm_location: Optional[str] = None
    total_land_hectares: Optional[Decimal] = None
    license_number: Optional[str] = None
    experience_years: Optional[int] = None
    wage_rate_per_hour: Optional[Decimal] = None
    wage_rate_per_hectare: Optional[Decimal] = None
    specialization: Optional[str] = None
    business_name: Optional[str] = None
    gst_number: Optional[str] = None
    bank_account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    upi_id: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None


class ProfileResponse(BaseModel):
    """
    Profile row for API responses.

    All attributes are optional so the same model works for every ``User.role``;
    clients can read the subset relevant to the role.
    """

    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = Field(default=None, description="Profile UUID as string.")
    user_id: Optional[str] = Field(default=None, description="Owning user UUID as string.")

    farm_name: Optional[str] = None
    farm_location: Optional[str] = None
    total_land_hectares: Optional[Decimal] = None
    license_number: Optional[str] = None
    experience_years: Optional[int] = None
    wage_rate_per_hour: Optional[Decimal] = None
    wage_rate_per_hectare: Optional[Decimal] = None
    specialization: Optional[str] = None
    business_name: Optional[str] = None
    gst_number: Optional[str] = None
    bank_account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    upi_id: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UserResponse(BaseModel):
    """Sanitized user record for API responses (no password hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="User UUID as string.")
    phone_number: str
    name: str
    email: Optional[str] = None
    role: str
    is_active: bool
    profile: Optional[ProfileResponse] = Field(
        default=None,
        description="Embedded profile when loaded; omit or leave null if not fetched.",
    )

    @computed_field
    @property
    def profile_completed(self) -> bool:
        """True when a profile is present and at least one non-meta field has a value."""
        if self.profile is None:
            return False
        data = self.profile.model_dump(exclude_none=True)
        for meta in ("id", "user_id", "created_at", "updated_at"):
            data.pop(meta, None)
        return bool(data)


class FarmerOptionResponse(BaseModel):
    """Compact farmer payload for session assignment pickers."""

    id: str
    name: str
    phone_number: str
    farm_name: Optional[str] = None
    farm_location: Optional[str] = None
    total_land_hectares: Optional[Decimal] = None


class LoginResponse(BaseModel):
    """Access and refresh tokens plus the authenticated user."""

    access_token: str
    refresh_token: str
    token_type: str = Field(default="Bearer", description="OAuth2 token type; always Bearer.")
    expires_in: int = Field(..., ge=0, description="Access token lifetime in seconds.")
    user: UserResponse
