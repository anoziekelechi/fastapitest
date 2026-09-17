
            
# CHANGE PASSWORD

async def request_password_change(
    data: RequestPasswordChange,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser,
) -> dict:
    """
    Step 1: Verify the current password, send an OTP to the user's email,
    and issue an anti-replay session token.

    Flow:
        1. Load the ORM user.
        2. Verify the supplied current password.
        3. Reject if the account is disabled or unverified.
        4. Send OTP to user.email (otp_type="change_password").
        5. Create the session token in Redis (owner = user_id).

    On OTP send failure the session token is NOT created, so there is
    no orphaned state.
    """

    # ------------------------------------------------------------------
    # 1. Load ORM user
    # ------------------------------------------------------------------
    user = await get_user_by_id(db, current_user.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 2. Verify current password
    # ------------------------------------------------------------------
    if not verify_password(data.current_password, user.hashed_password):
        logger.warning(
            "Failed password verification for password change request: user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    # ------------------------------------------------------------------
    # 3. Account-state guards (defense in depth)
    # ------------------------------------------------------------------
    if user.disabled:
        logger.warning(
            "Disabled user requested password change: user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
        )

    if not user.verified:
        logger.info(
            "Unverified user requested password change: user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified. Please verify your email first.",
        )

    # ------------------------------------------------------------------
    # 4. Send OTP (may raise 429 from internal cooldown)
    # ------------------------------------------------------------------
    try:
        await generate_and_send_otp(
            user=user,
            db=db,
            otp_type="change_password",
            subject="Confirm your password change",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
        )
    except HTTPException as e:
        # Bubble up 429 (cooldown) — frontend needs to surface it
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise

        logger.error(
            "Failed to send change_password OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error sending change_password OTP for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )


    # ------------------------------------------------------------------
    # 5. Anti-replay session token (only after OTP is stored)
    # ------------------------------------------------------------------
    token = secrets.token_urlsafe(32)
    change_password_key = f"change_password_attempt:{token}"
    session_ttl = int(
        timedelta(
            minutes=OTP_EXPIRE_MINUTES
        ).total_seconds()
    )
    await redis.set(
        change_password_key,
        str(user_id),
        ex=session_ttl,
    )

    logger.info(
        "Password change OTP sent for user_id=%s",
        user_id,
    )

    return {
        "message": (
            "An OTP has been sent to your email. "
            "Enter it below along with your new password."
        ),
        "change_password_token": token,
    }




async def confirm_password_change(
    data: ConfirmPasswordChange,
    db: AsyncSession,
    redis: Redis,
    current_user: ReadUser,
) -> dict:
    """
    Step 2: Verify the OTP and set the new password.

    Flow:
        1. Load ORM user.
        2. Validate session token → must exist and belong to caller.
        3. Re-check account state (verified/disabled).
        4. Reject if new password equals current password.
        5. Atomically verify + consume OTP.
        6. Update hashed_password and commit.
        7. Revoke all refresh tokens (forces re-login everywhere).
        8. Delete session token.
        9. Return message.
    """

    # ------------------------------------------------------------------
    # 1. Load ORM user
    # ------------------------------------------------------------------
    user = await get_user_by_id(db, current_user.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 2. Validate session token
    # ------------------------------------------------------------------

    session_key = f"change_password_attempt:{data.change_password_token}"
    stored_id = await redis.get(session_key)

    if not stored_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_SESSION_EXPIRED,
        )

    if isinstance(stored_id, bytes):
        stored = stored_id.decode("utf-8")

    try:
        stored_user_id = int(stored)
    except (TypeError, ValueError):
        await redis.delete(session_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_SESSION_EXPIRED,
        )

    if stored_user_id != user_id:
        logger.warning(
            "change_password token mismatch: token_owner=%s, requester=%s",
            stored_user_id,
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_SESSION_EXPIRED,
        )

    # ------------------------------------------------------------------
    # 3. Re-check account state
    # ------------------------------------------------------------------
    if user.disabled:
        await redis.delete(session_key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
        )

    if not user.verified:
        await redis.delete(session_key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified. Please verify your email first.",
        )

    # ------------------------------------------------------------------
    # 4. Reject same-password (no-op change)
    # ------------------------------------------------------------------
    if verify_password(data.new_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from your current password",
        )

    # ------------------------------------------------------------------
    # 5. Atomically verify + consume OTP
    # ------------------------------------------------------------------
    otp_key = f"otp:{user_id}:change_password"
    otp_valid = await verify_and_consume_otp(
        redis=redis,
        otp_key=otp_key,
        submitted_otp=data.otp_code,
    )

    if not otp_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    # ------------------------------------------------------------------
    # 6. Persist new password
    # ------------------------------------------------------------------
    user.hashed_password = hash_password(data.new_password)
    db.add(user)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to update password for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update password. Please try again.",
        )

    # ------------------------------------------------------------------
    # 7. Revoke all refresh tokens
    #
    # Failing here is a security event: password is changed but old
    # sessions may still be valid. Log critical and surface 500.
    # ------------------------------------------------------------------
    try:
        await revoke_all_user_tokens(user_id, redis)
    except Exception:
        logger.critical(
            "CRITICAL: password changed but token revocation failed for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Password was changed, but active sessions could not be "
                "fully revoked. Please contact support."
            ),
        )

    # ------------------------------------------------------------------
    # 8. Consume session token
    # ------------------------------------------------------------------
    try:
        await redis.delete(session_key)
    except Exception:
        logger.exception(
            "Failed to delete change_password session for user_id=%s",
            user_id,
        )

    logger.info("Password changed for user_id=%s", user_id)

    return {"message": "Password updated successfully"}

 




router = APIRouter(prefix="/me", tags=["me"])


@router.post(
    "/password/request",
    response_model=RequestPasswordChangeResponse,
    status_code=status.HTTP_200_OK,
)
async def request_password_change_route(
    data: RequestPasswordChange,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    mailer=Depends(get_mailer),
    current_user: ReadUser = Depends(get_current_user),
):
    return await request_password_change(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/password/confirm",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def confirm_password_change_route(
    data: ConfirmPasswordChange,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: ReadUser = Depends(get_current_user),
):
    return await confirm_password_change(
        data=data,
        db=db,
        redis=redis,
        current_user=current_user,
    )

# app/utils/email_change.py

import json
from datetime import timedelta

from redis.asyncio import Redis
from fastapi import HTTPException, status

from app.core.config import OTP_EXPIRE_MINUTES
from app.utils.email import normalize_email


SESSION_EXPIRED_DETAIL = (
    "Email change session expired or invalid. "
    "Please request a new OTP."
)


def email_change_key(token: str) -> str:
    return f"email_change:{token}"


def build_email_change_session(
    user_id: int,
    new_email: str,
) -> str:
    """
    Canonical JSON payload for an email-change session.

    Writer: request_email_change()
    Readers: resend_otp(), verify_new_email()
    """
    return json.dumps(
        {
            "user_id": user_id,
            "new_email": normalize_email(new_email),
        }
    )


def session_ttl_seconds() -> int:
    return int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds())


async def parse_email_change_session(
    redis: Redis,
    token: str,
    *,
    delete_on_error: bool = True,
) -> tuple[int, str]:
    """
    Load and validate an email-change session from Redis.

    Returns (user_id, new_email).

    Raises HTTPException(400, SESSION_EXPIRED_DETAIL) on any failure.
    If delete_on_error is True, a corrupted session is purged.
    """
    key = email_change_key(token)

    stored = await redis.get(key)
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=SESSION_EXPIRED_DETAIL,
        )

    if isinstance(stored, bytes):
        stored = stored.decode("utf-8")

    try:
        session = json.loads(stored)
        user_id = int(session["user_id"])
        new_email = normalize_email(session["new_email"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        if delete_on_error:
            await redis.delete(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=SESSION_EXPIRED_DETAIL,
        )

    if not new_email:
        if delete_on_error:
            await redis.delete(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=SESSION_EXPIRED_DETAIL,
        )

    return user_id, new_email




from pydantic import BaseModel, EmailStr, Field
from datetime import datetime


from typing import Literal
from pydantic import BaseModel, EmailStr, Field

OtpType = Literal["registration", "login", "email_change", "password_reset"]


class ResendOtpRequest(BaseModel):
    email: EmailStr
    account_token: str = Field(..., min_length=10)
    otp_type: OtpType


class MessageResponse(BaseModel):
    message: str

class CreateUser(BaseModel):
    surname: str = Field(..., min_length=2, max_length=100)
    othernames: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    password: str = Field(..., min_length=6)
    country_id: int


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    account_token: str = Field(..., min_length=10)
    otp_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class ResendOtpRequest(BaseModel):
    email: EmailStr
    account_token: str = Field(..., min_length=10)


class ReadUser(BaseModel):
    id: int
    surname: str
    othernames: str
    email: EmailStr
    country_id: int
    is_admin: bool
    disabled: bool
    verified: bool
    date_verified: datetime | None = None

    model_config = {"from_attributes": True}


class RegisterResponse(BaseModel):
    message: str
    email: EmailStr
    reg_token: str


class VerifyRegistrationResponse(BaseModel):
    message: str
    user: ReadUser


class MessageResponse(BaseModel):
    message: str




import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status, BackgroundTasks
from fastapi_mail import FastMail
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.country import Country
from app.schemas.user import (
    CreateUser,
    ReadUser,
    VerifyOtpRequest,
    ResendOtpRequest,
)
from app.core.security import hash_password
from app.core.config import OTP_EXPIRE_MINUTES
from app.utils.otp import generate_and_send_otp, verify_and_consume_otp
from app.utils.users import get_user_by_email, get_user_id
from app.core.logging import logger


# =============================================================================
# REGISTER USER
# =============================================================================

async def register_user(
    data: CreateUser,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Register a new user (unverified) and send OTP.

    - Blocks already-authenticated users
    - Validates email uniqueness and country
    - Creates the user as unverified
    - Sends OTP (hashed in Redis via generate_and_send_otp)
    - Creates anti-replay registration token only after OTP succeeds
    - Rolls back user row if OTP delivery fails (avoids dead-end state)
    """
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Logged in user cannot create account",
        )

    # ------------------------------------------------------------------
    # 1. Validate country
    # ------------------------------------------------------------------
    if data.country_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Country is required",
        )

    country = await db.get(Country, data.country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Country not found",
        )

    # ------------------------------------------------------------------
    # 2. Email uniqueness
    # ------------------------------------------------------------------
    existing = await get_user_by_email(db, data.email)
    if existing:
        if existing.verified:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )

        # Unverified account exists — allow re-registration by refreshing
        # credentials and re-issuing OTP instead of dead-ending the user.
        existing.surname = data.surname
        existing.othernames = data.othernames
        existing.hashed_password = hash_password(data.password)
        existing.country_id = data.country_id

        db.add(existing)
        await db.commit()
        await db.refresh(existing)

        new_user = existing

    else:
        # ------------------------------------------------------------------
        # 3. Create unverified user
        # ------------------------------------------------------------------
        new_user = User(
            surname=data.surname,
            othernames=data.othernames,
            email=data.email,
            hashed_password=hash_password(data.password),
            country_id=data.country_id,
            is_admin=False,
            disabled=False,
            verified=False,
            one_click=False,
            payment_id=None,
        )

        db.add(new_user)
        try:
            await db.commit()
            await db.refresh(new_user)
        except Exception:
            await db.rollback()
            logger.exception("Unexpected error while creating user")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to complete your registration",
            )

    user_id = get_user_id(new_user)

    # ------------------------------------------------------------------
    # 4. Send OTP first (stores otp:{user_id}:registration → SHA-256)
    # ------------------------------------------------------------------
    try:
        await generate_and_send_otp(
            user=new_user,
            otp_type="registration",
            subject="Verify your account",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
        )
    except HTTPException as e:
        # 429 (cooldown) is safe to bubble up — user row stays,
        # user can retry via resend endpoint.
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise

        logger.error(
            "Failed to send registration OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )

        # Roll back the newly created user so the user isn't dead-ended.
        await _safe_delete_user(db, new_user)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error while sending registration OTP for user_id=%s",
            user_id,
        )
        await _safe_delete_user(db, new_user)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    # ------------------------------------------------------------------
    # 5. Create anti-replay token ONLY after OTP succeeds
    # ------------------------------------------------------------------
    reg_token = secrets.token_urlsafe(32)

    await redis.set(
        f"reg_attempt:{reg_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info(
        "New registration started for %s (user_id=%s)",
        new_user.email,
        user_id,
    )

    return {
        "message": "OTP sent to your email",
        "email": new_user.email,
        "reg_token": reg_token,
    }


async def _safe_delete_user(db: AsyncSession, user: User) -> None:
    """Best-effort delete of a user row during failure cleanup."""
    try:
        await db.delete(user)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to roll back user row during OTP failure cleanup"
        )


# =============================================================================
# VERIFY REGISTRATION OTP
# =============================================================================

async def verify_registration_otp(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: Redis,
) -> dict:
    """
    Step 2: Verify registration OTP and mark the user as verified.
    Returns a dict with `message` and `user` for the frontend.
    """
    # ------------------------------------------------------------------
    # 1. Get user
    # ------------------------------------------------------------------
    user = await get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already verified",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 2. Validate registration session (do NOT delete yet)
    # ------------------------------------------------------------------
    reg_key = f"reg_attempt:{data.account_token}"
    stored_id = await redis.get(reg_key)

    if not stored_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid. Please request a new code.",
        )

    if isinstance(stored_id, bytes):
        stored_id = stored_id.decode("utf-8")

    try:
        stored_user_id = int(stored_id)
    except (TypeError, ValueError):
        await redis.delete(reg_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid. Please request a new code.",
        )

    if stored_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid. Please request a new code.",
        )

    # ------------------------------------------------------------------
    # 3. Atomically verify + consume hashed OTP
    # ------------------------------------------------------------------
    otp_key = f"otp:{user_id}:registration"

    otp_valid = await verify_and_consume_otp(
        redis=redis,
        otp_key=otp_key,
        submitted_otp=data.otp_code,
    )
    if not otp_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    # ------------------------------------------------------------------
    # 4. Consume registration session
    # ------------------------------------------------------------------
    await redis.delete(reg_key)

    # ------------------------------------------------------------------
    # 5. Mark user as verified
    # ------------------------------------------------------------------
    user.verified = True
    user.date_verified = datetime.now(timezone.utc)

    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info("Registration verified for user_id=%s", user_id)

    return {
        "message": "Account verified successfully",
        "user": ReadUser.model_validate(user),
    }


# =============================================================================
# RESEND REGISTRATION OTP
# =============================================================================

async def resend_registration_otp(
    data: ResendOtpRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Resend OTP for an existing unverified registration.

    - Validates anti-replay session token (reg_attempt) still exists
    - Ensures token belongs to the user
    - Sends a fresh OTP (cooldown enforced inside generate_and_send_otp)
    """
    user = await get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already verified",
        )

    user_id = get_user_id(user)

    reg_key = f"reg_attempt:{data.account_token}"
    stored_id = await redis.get(reg_key)

    if not stored_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid. Please register again.",
        )

    if isinstance(stored_id, bytes):
        stored_id = stored_id.decode("utf-8")

    try:
        stored_user_id = int(stored_id)
    except (TypeError, ValueError):
        await redis.delete(reg_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid. Please register again.",
        )

    if stored_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid. Please register again.",
        )

    try:
        await generate_and_send_otp(
            user=user,
            otp_type="registration",
            subject="Verify your account",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
        )
    except HTTPException as e:
        # Bubble up 429 cooldown so frontend can show retry-after
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(
            "Failed to resend registration OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error while resending registration OTP for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    logger.info("Registration OTP resent for user_id=%s", user_id)

    return {"message": "A new OTP has been sent to your email"}








import json
from datetime import timedelta
from typing import Literal

from fastapi import HTTPException, status, BackgroundTasks
from fastapi_mail import FastMail
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.core.config import OTP_EXPIRE_MINUTES
from app.utils.otp import generate_and_send_otp
from app.utils.users import get_user_by_email, get_user_id
from app.utils.email import normalize_email
from app.core.logging import logger


OtpType = Literal["registration", "login", "email_change", "password_reset"]

_SESSION_EXPIRED = "Session expired - please start over"


# =============================================================================
# RESEND OTP
# =============================================================================

async def resend_otp(
    data,  # ResendOtpRequest: email, account_token, otp_type
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Resend OTP for an active authentication flow.

    Supported flows:

        registration
            Session:  reg_attempt:{token}
            Rule:     user must still be unverified.

        login
            Session:  login_attempt:{token}
            Rule:     user must still be verified AND active.
            Note:     session is created only for verified + active users,
                      but we re-check state as defense-in-depth.

        email_change
            Session:  email_change:{token}
            Value:    JSON { "user_id": int, "new_email": str }
            Rule:     user is resolved from the session,
                      NOT from client-supplied email.
            OTP sent to: session.new_email

        password_reset
            Session:  reset_attempt:{token}
            Rule:     user must still satisfy password-reset flow rules.

    Notes:
        - The existing session token is NOT replaced or deleted on success.
        - On successful resend the session TTL is refreshed so the user
          isn't cut off mid-flow.
        - A new OTP overwrites the previous OTP hash for (user_id, otp_type).
    """

    # =========================================================================
    # EMAIL CHANGE — user + new_email come entirely from the Redis session
    # =========================================================================
    if data.otp_type == "email_change":

        token_key = f"email_change:{data.account_token}"
        stored = await redis.get(token_key)

        if not stored:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_SESSION_EXPIRED,
            )

        if isinstance(stored, bytes):
            stored = stored.decode("utf-8")

        try:
            session = json.loads(stored)
            user_id = int(session["user_id"])
            new_email = normalize_email(session["new_email"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            await redis.delete(token_key)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_SESSION_EXPIRED,
            )

        if not new_email:
            await redis.delete(token_key)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_SESSION_EXPIRED,
            )

        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Send OTP to the session-owned new email (never trust client email)
        await generate_and_send_otp(
            user=user,
            otp_type="email_change",
            subject="Verify your new email address",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
            override_email=new_email,
        )

        await _refresh_session_ttl(redis, token_key)

        logger.info(
            "Resent email_change OTP for user_id=%s (target=%s)",
            user_id,
            new_email,
        )

        return {"message": "A new OTP has been sent to your email"}

    # =========================================================================
    # REGISTRATION / LOGIN / PASSWORD_RESET — look up by client email
    # =========================================================================
    email = normalize_email(data.email)
    user = await get_user_by_email(db, email)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    # -------------------------------------------------------------------------
    # Resolve flow-specific rules + session key
    # -------------------------------------------------------------------------
    if data.otp_type == "registration":

        if user.verified:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account already verified",
            )

        token_key = f"reg_attempt:{data.account_token}"
        subject = "Verify your account"

    elif data.otp_type == "login":

        # Defense-in-depth: state may have changed since initiate_login()
        if user.disabled:
            logger.warning(
                "Disabled user attempted login OTP resend: user_id=%s",
                user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account suspended. Please contact admin.",
            )

        if not user.verified:
            logger.info(
                "Unverified user attempted login OTP resend: user_id=%s",
                user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account not verified. Please verify your email first.",
            )

        token_key = f"login_attempt:{data.account_token}"
        subject = "Your login OTP"

    elif data.otp_type == "password_reset":

        token_key = f"reset_attempt:{data.account_token}"
        subject = "Reset your password"

    else:
        # Unreachable if OtpType Literal is enforced upstream,
        # but kept for safety against direct calls.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OTP type for resend",
        )

    # -------------------------------------------------------------------------
    # Validate session token (do NOT delete on success — only refresh TTL)
    # -------------------------------------------------------------------------
    stored = await redis.get(token_key)

    if not stored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_SESSION_EXPIRED,
        )

    if isinstance(stored, bytes):
        stored = stored.decode("utf-8")

    try:
        stored_user_id = int(stored)
    except (TypeError, ValueError):
        await redis.delete(token_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_SESSION_EXPIRED,
        )

    if stored_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_SESSION_EXPIRED,
        )

    # -------------------------------------------------------------------------
    # Send new OTP (overwrites previous hash for this user + otp_type)
    # -------------------------------------------------------------------------
    await generate_and_send_otp(
        user=user,
        otp_type=data.otp_type,
        subject=subject,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        override_email=None,
    )

    # -------------------------------------------------------------------------
    # Refresh session TTL so the user isn't cut off mid-flow
    # -------------------------------------------------------------------------
    await _refresh_session_ttl(redis, token_key)

    logger.info(
        "Resent %s OTP for user_id=%s",
        data.otp_type,
        user_id,
    )

    return {"message": "A new OTP has been sent to your email"}


# =============================================================================
# HELPERS
# =============================================================================

async def _refresh_session_ttl(redis: Redis, key: str) -> None:
    """
    Extend a session token's TTL after a successful resend.

    Rationale:
        A user who keeps re-requesting OTPs should not have the
        underlying session expire underneath them. We only extend,
        never shorten — if the existing TTL is longer, keep it.
    """
    try:
        ttl = await redis.ttl(key)
    except Exception:
        logger.exception("Failed to read TTL for session key=%s", key)
        return

    if ttl is None or ttl < 0:
        # -1 = no expiry, -2 = key vanished. Nothing to do.
        return

    fresh = int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds())

    try:
        await redis.expire(key, max(ttl, fresh))
    except Exception:
        logger.exception("Failed to refresh TTL for session key=%s", key)
