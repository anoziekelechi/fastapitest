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



def validate_surname(value: str) -> str:
    """
    Single word, letters only, no spaces.
    "Njoku" → "NJOKU"
    "Njoku Kelechi" → Error
    """
    if not value or not value.strip():
        raise ValueError("Surname cannot be empty")
    
    cleaned = value.strip()
    
    if not re.fullmatch(r"[A-Za-z]+", cleaned):
        if " " in cleaned:
            raise ValueError(
                "Surname must be a single word with no spaces. "
                "Example: 'Njoku' not 'Njoku Kelechi'"
            )
        raise ValueError(
            "Surname must contain only letters (A-Z). "
            "No numbers, spaces, or special characters allowed."
        )
    
    return cleaned.upper()


def validate_othernames(value: str) -> str:
    """
    Letters and spaces only. Multiple spaces collapsed to one.
    "Kelechi Chukwunonye" → "KELECHI CHUKWUNONYE"
    "Kelechi  Chukwunonye" → "KELECHI CHUKWUNONYE"
    """
    if not value or not value.strip():
        raise ValueError("Othernames cannot be empty")
    
    stripped = value.strip()
    cleaned = re.sub(r" +", " ", stripped)      # collapse spaces only
    
    if not re.fullmatch(r"[A-Za-z]+( [A-Za-z]+)*", cleaned):  # ✅ correct regex
        raise ValueError(
            "Othernames must contain only letters and spaces. "
            "No numbers or special characters allowed."
        )
    
    return cleaned.upper()




def validate_name(value: str, field: str) -> str:
    """
    Routes to the correct name validator based on field label.
    
    Used ONLY by scripts/create_admin.py for interactive prompts.
    Schemas call validate_surname() and validate_othernames() directly.
    
    Args:
        value: The name string to validate
        field: "Surname" or "Othernames" (case-insensitive)
        
    Raises:
        ValueError: If field is not "surname" or "othernames"
    """
    field_lower = field.lower()
    
    if field_lower == "surname":
        return validate_surname(value)
    
    if field_lower == "othernames":
        return validate_othernames(value)
    
    # Should never reach here - but clear error if it does
    raise ValueError(
        f"Unknown field '{field}'. "
        f"Expected 'surname' or 'othernames'."
    )

def normalize_email(value: EmailStr) -> str:
    return value.lower()
    
    


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
    def normalize_user_email(cls, v: str) -> str:
        return normalize_email(v)
    
    @field_validator("surname", mode="before")
    @classmethod
    def validate_user_surname(cls, v: str) -> str:
        return validate_surname(v)
    
    @field_validator("othernames", mode="before")
    @classmethod
    def validate_othernames(cls, v: str) -> str:
        return validate_othernames(v)
    
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
    def normalize_login_email(cls, v: str) -> str:
        return normalize_email(v)


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
        return validate_surname(v) if v else None
    
    @field_validator("othernames", mode="before")
    @classmethod
    def validate_othernames(cls, v: str | None) -> str | None:
        return validate_othernames(v) if v else None


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
    
    
class TokenData(BaseModel):
    access_token: str
    refresh_token: str
    csrf_token: str


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
    
    
class CSRFData(BaseModel):
    user_id: int
    expires_at: int
    
    
    

class ResendOtpRequest(BaseModel):
    """Schema for resending an OTP."""
    model_config = ConfigDict(extra="forbid")
    
    email: EmailStr
    account_token: str = Field(..., min_length=32)  # Same anti-replay token from step 1
    otp_type: Literal["registration", "login"]
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return normalize_email(v)
    
    
# Reset Password

class RequestResetPassword(BaseModel):
    """Schema for requesting a password reset OTP."""
    model_config = ConfigDict(extra="forbid")
    
    email: EmailStr
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return normalize_email(v)


class ResetPassword(BaseModel):
    """Schema for completing a password reset."""
    model_config = ConfigDict(extra="forbid")
    
    email: EmailStr
    otp_code: str = Field(..., pattern=r"^\d{6}$")
    reset_token: str = Field(..., min_length=32)  # Anti-replay token
    new_password: str
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return normalize_email(v)
    
    @field_validator("new_password", mode="before")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return validate_password(v)




# Change Email
class RequestEmailChange(BaseModel):
    """Schema for requesting an email change."""
    model_config = ConfigDict(extra="forbid")
    
    current_password: str = Field(..., min_length=8)
    new_email: EmailStr
    
    @field_validator("new_email", mode="before")
    @classmethod
    def normalize_new_email(cls, v: str) -> str:
        return normalize_email(v)


class VerifyEmailChange(BaseModel):
    """Schema for verifying the new email OTP."""
    model_config = ConfigDict(extra="forbid")
    
    otp_code: str = Field(..., pattern=r"^\d{6}$")
    email_change_token: str = Field(..., min_length=32)

