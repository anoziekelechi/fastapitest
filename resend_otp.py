

import hashlib
import json
import logging
import secrets
from datetime import timedelta

from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

# Adjust imports to your project
# from api.users.models import User
# from api.users.utils import get_user_id
# from api.users.logic import get_user_by_email
# from api.core.email import normalize_email
# from api.core.otp_config import (
#     OTP_LENGTH, OTP_EXPIRE_MINUTES, OTP_RATE_LIMIT, OTP_RATE_WINDOW,
# )
# from api.core.email_sender import send_otp
# from api.auth.schemas import ResendOtpRequest

logger = logging.getLogger(__name__)


# =============================================================================
# OTP HELPERS
# =============================================================================

def generate_otp() -> str:
    """Generate a numeric OTP."""
    return "".join(
        str(secrets.randbelow(10))
        for _ in range(OTP_LENGTH)
    )


def hash_otp(otp: str) -> str:
    """Hash OTP before storing in Redis. Plaintext is never stored."""
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


# =============================================================================
# ATOMIC VERIFY + CONSUME
# =============================================================================

VERIFY_AND_CONSUME_OTP_SCRIPT = r"""
local stored_hash = redis.call("GET", KEYS[1])

if not stored_hash then
    return 0
end

if stored_hash == ARGV[1] then
    redis.call("DEL", KEYS[1])
    return 1
end

return 2
"""


async def verify_and_consume_otp(
    redis: Redis,
    otp_key: str,
    submitted_otp: str,
) -> bool:
    """
    Atomically verify and consume a hashed OTP.

    Returns:
        True  → OTP valid and consumed
        False → missing, invalid, or already used
    """
    submitted_hash = hash_otp(submitted_otp)
    script = redis.register_script(VERIFY_AND_CONSUME_OTP_SCRIPT)
    result = await script(keys=[otp_key], args=[submitted_hash])
    return result == 1


# =============================================================================
# GENERATE + SEND
# =============================================================================

async def generate_and_send_otp(
    user: User,
    otp_type: str,
    subject: str,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    override_email: str | None = None,
) -> None:
    """
    Generate OTP, store SHA-256 hash in Redis, then queue email.

    Redis key:   otp:{user_id}:{otp_type}
    Redis value: SHA-256(otp)

    Supported types:
        registration, login, email_change, password_reset
    """
    allowed = {
        "registration",
        "login",
        "email_change",
        "password_reset",
    }
    if otp_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OTP type",
        )

    user_id = get_user_id(user)

    # Rate limit per user + type
    rate_key = f"otp_rate:{user_id}:{otp_type}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, OTP_RATE_WINDOW)
    if count > OTP_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Try again in 1 hour.",
        )

    otp = generate_otp()
    otp_hash = hash_otp(otp)
    otp_key = f"otp:{user_id}:{otp_type}"

    await redis.set(
        otp_key,
        otp_hash,
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    recipient = override_email if override_email is not None else user.email

    logger.info(
        "OTP stored for user_id=%s (type=%s). Queuing email...",
        user_id,
        otp_type,
    )

    background_tasks.add_task(
        send_otp,
        email=recipient,
        otp=otp,
        subject=subject,
        otp_type=otp_type,
        mailer=mailer,
    )


# =============================================================================
# RESEND OTP
# =============================================================================

async def resend_otp(
    data: ResendOtpRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Resend OTP for an in-progress authentication flow.

    Supported flows and session keys:

        registration  → reg_attempt:{token}
            - User must still be unverified
            - Used after register / resend_verification

        login         → login_attempt:{token}
            - Only exists for verified + active users
              (initiate_login blocks disabled/unverified before OTP)
            - Resend is only for users already on the login OTP screen

        email_change  → email_change:{token}
            - Session holds user_id + new_email
            - OTP is sent to the new email

    The original session token is NOT deleted on resend.
    Unverified users who need a verification email must use
    resend_verification, not this login resend path.
    """
    user = await get_user_by_email(db, data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)
    override_email: str | None = None

    # ------------------------------------------------------------------
    # Resolve flow + session key
    # ------------------------------------------------------------------
    if data.otp_type == "registration":
        if user.verified:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account already verified",
            )
        token_key = f"reg_attempt:{data.account_token}"
        subject = "Verify your account"

    elif data.otp_type == "login":
        # login_attempt is only created for verified + active users
        # by initiate_login. No extra status checks needed here.
        token_key = f"login_attempt:{data.account_token}"
        subject = "Your login OTP"

    elif data.otp_type == "email_change":
        token_key = f"email_change:{data.account_token}"
        subject = "Verify your new email address"

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OTP type for resend",
        )

    # ------------------------------------------------------------------
    # Validate session token
    # ------------------------------------------------------------------
    stored = await redis.get(token_key)
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired - please start over",
        )

    if isinstance(stored, bytes):
        stored = stored.decode("utf-8")

    if data.otp_type == "email_change":
        try:
            session = json.loads(stored)
            stored_user_id = int(session["user_id"])
            new_email = normalize_email(session["new_email"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            await redis.delete(token_key)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session expired - please start over",
            )

        if stored_user_id != user_id or not new_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session expired - please start over",
            )

        override_email = new_email

    else:
        # registration / login → value is plain user_id
        try:
            stored_user_id = int(stored)
        except (TypeError, ValueError):
            await redis.delete(token_key)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session expired - please start over",
            )

        if stored_user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session expired - please start over",
            )

    # ------------------------------------------------------------------
    # Issue new OTP (overwrites previous hash for this user + type)
    # ------------------------------------------------------------------
    await generate_and_send_otp(
        user=user,
        otp_type=data.otp_type,
        subject=subject,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        override_email=override_email,
    )

    return {"message": "A new OTP has been sent to your email"}
