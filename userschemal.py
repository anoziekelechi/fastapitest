"""User schemas."""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator


# =============================================================================
# SHARED VALIDATORS
# =============================================================================

def validate_password(value: str) -> str:
    """Enforce strong password policy."""
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters")
    if not re.search(r"[A-Z]", value):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", value):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"[0-9]", value):
        raise ValueError("Password must contain at least one digit")
    if not re.search(r"[@#&_+\-]", value):
        raise ValueError("Password must contain at least one special character (@#&_-+)")
    return value


def validate_name(value: str, field: str) -> str:
    """Enforce name format - letters only, uppercase."""
    if not value or not value.strip():
        raise ValueError(f"{field} cannot be empty")
    cleaned = re.sub(r"\s+", " ", value.strip())
    if not re.fullmatch(r"[A-Za-z ]+", cleaned):
        raise ValueError(f"{field} must contain only letters")
    return cleaned.upper()


# =============================================================================
# REQUEST SCHEMAS
# =============================================================================

class CreateUser(BaseModel):
    """Schema for user registration."""
    model_config = ConfigDict(extra="forbid")
    
    surname: str
    othernames: str
    email: EmailStr
    password: str
    country_id: int = Field(gt=0)
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()
    
    @field_validator("surname", mode="before")
    @classmethod
    def validate_surname(cls, v: str) -> str:
        return validate_name(v, "Surname")
    
    @field_validator("othernames", mode="before")
    @classmethod
    def validate_othernames(cls, v: str) -> str:
        return validate_name(v, "Othernames")
    
    @field_validator("password", mode="before")
    @classmethod
    def validate_user_password(cls, v: str) -> str:
        return validate_password(v)


class LoginRequest(BaseModel):
    """Schema for login step 1 (email + password)."""
    email: EmailStr
    password: str = Field(..., min_length=8)
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()


class VerifyOtpRequest(BaseModel):
    """Schema for OTP verification (registration + login)."""
    email: EmailStr
    otp_code: str = Field(..., pattern=r"^\d{6}$")
    account_token: str = Field(..., min_length=32)  # Anti-replay token


class UpdateNames(BaseModel):
    """Schema for updating user names."""
    model_config = ConfigDict(extra="forbid")
    
    surname: str | None = None
    othernames: str | None = None
    
    @field_validator("surname", mode="before")
    @classmethod
    def validate_surname(cls, v: str | None) -> str | None:
        return validate_name(v, "Surname") if v else None
    
    @field_validator("othernames", mode="before")
    @classmethod
    def validate_othernames(cls, v: str | None) -> str | None:
        return validate_name(v, "Othernames") if v else None


class UpdatePassword(BaseModel):
    """Schema for password change."""
    model_config = ConfigDict(extra="forbid")
    
    current_password: str
    new_password: str
    
    @field_validator("new_password", mode="before")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return validate_password(v)


class VerifyPassword(BaseModel):
    """Schema for password confirmation (e.g., account deletion)."""
    password: str


# =============================================================================
# RESPONSE SCHEMAS
# =============================================================================

class ReadUser(BaseModel):
    """User response schema."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    surname: str
    othernames: str
    email: str
    country_id: int
    is_admin: bool
    verified: bool
    disabled: bool
    date_verified: datetime | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    """Token response - tokens set as cookies, this confirms success."""
    message: str = "Login successful"


# =============================================================================
# JWT SCHEMAS
# =============================================================================

class TokenPayload(BaseModel):
    """JWT token payload."""
    model_config = ConfigDict(extra="forbid")
    
    sub: int                                    # User ID
    iat: int                                    # Issued at
    exp: int                                    # Expires at
    jti: str                                    # JWT ID
    token_type: Literal["access", "refresh"]


class RefreshTokenPayload(BaseModel):
    """Refresh token payload."""
    model_config = ConfigDict(extra="forbid")
    
    sub: int
    iat: int
    exp: int
    jti: str
    token_type: Literal["refresh"] = "refresh"
