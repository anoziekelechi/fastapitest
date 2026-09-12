from typing import Literal
OtpType = Literal["registration", "login", "email_change", "password_reset"]

class ResendOtpRequest(BaseModel):
    email: EmailStr
    account_token: str
    otp_type: OtpType



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
