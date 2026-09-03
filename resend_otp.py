
# chat got
async def resend_verification(
    data: ResendVerificationRequest,   # simple schema: just email
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Resend verification OTP to an unverified user.

    This is used when a user tries to log in and receives
    status = "unverified". It does NOT require an old
    registration session token.
    """

    user = await get_user_by_email(db, data.email)

    # Generic response to avoid account enumeration
    generic = {
        "message": "If an unverified account exists for this email, "
                   "a new verification OTP has been sent.",
    }

    if not user:
        return generic

    if user.disabled:
        # Still return generic – don't reveal the account is disabled here
        return generic

    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is already verified. Please login.",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # Send a fresh registration OTP
    # ------------------------------------------------------------------
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
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(
            "Failed to resend verification OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        return generic
    except Exception:
        logger.exception(
            "Unexpected error while resending verification OTP for user_id=%s",
            user_id,
        )
        return generic

    # ------------------------------------------------------------------
    # Create a new anti-replay registration token
    # ------------------------------------------------------------------
    reg_token = secrets.token_urlsafe(32)

    await redis.set(
        f"reg_attempt:{reg_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info("Verification OTP resent for user_id=%s", user_id)

    return {
        "message": "A new verification OTP has been sent to your email.",
        "email": user.email,
        "reg_token": reg_token,          # frontend needs this for verify step
    }






import hashlib
import json
import logging
import secrets
from datetime import timedelta

from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# =============================================================================
# OTP HELPERS
# =============================================================================

def generate_otp() -> str:
    """Generate a cryptographically secure numeric OTP."""
    return "".join(
        str(secrets.randbelow(10))
        for _ in range(OTP_LENGTH)
    )


def hash_otp(otp: str) -> str:
    """
    Hash OTP before storing it in Redis.

    Plaintext OTP is never stored in Redis.
    """
    return hashlib.sha256(
        otp.encode("utf-8")
    ).hexdigest()


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
        True  -> OTP is valid and consumed.
        False -> OTP is missing, invalid, expired, or already used.
    """

    submitted_hash = hash_otp(submitted_otp)

    script = redis.register_script(
        VERIFY_AND_CONSUME_OTP_SCRIPT
    )

    result = await script(
        keys=[otp_key],
        args=[submitted_hash],
    )

    return result == 1


# =============================================================================
# GENERATE + SEND OTP
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
    Generate OTP, store its SHA-256 hash in Redis, then queue the email.

    Redis key:
        otp:{user_id}:{otp_type}

    Redis value:
        SHA-256(otp)

    Supported types:
        registration
        login
        email_change
        password_reset
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

    # =========================================================================
    # RATE LIMIT
    # =========================================================================

    rate_key = f"otp_rate:{user_id}:{otp_type}"

    count = await redis.incr(rate_key)

    if count == 1:
        await redis.expire(
            rate_key,
            OTP_RATE_WINDOW,
        )

    if count > OTP_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Try again in 1 hour.",
        )

    # =========================================================================
    # GENERATE OTP
    # =========================================================================

    otp = generate_otp()
    otp_hash = hash_otp(otp)

    otp_key = f"otp:{user_id}:{otp_type}"

    # =========================================================================
    # STORE HASH
    #
    # SET overwrites any previous OTP for this user + type.
    #
    # Therefore:
    #
    # old OTP → immediately invalid
    # new OTP → becomes the only valid OTP
    # =========================================================================

    await redis.set(
        otp_key,
        otp_hash,
        ex=int(
            timedelta(
                minutes=OTP_EXPIRE_MINUTES
            ).total_seconds()
        ),
    )

    recipient = (
        override_email
        if override_email is not None
        else user.email
    )

    logger.info(
        "OTP stored for user_id=%s (type=%s). "
        "Queuing email...",
        user_id,
        otp_type,
    )

    # =========================================================================
    # QUEUE EMAIL
    #
    # Plaintext OTP exists only in application memory.
    # It is never stored in Redis.
    # =========================================================================

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
    Resend OTP for an active authentication flow.

    Supported flows:

        registration
            Session:
                reg_attempt:{token}

            User must still be unverified.

        login
            Session:
                login_attempt:{token}

            Session is created only for verified + active users.

        email_change
            Session:
                email_change:{token}

            Session contains:
                user_id
                new_email

            OTP is sent to the new email.

        password_reset
            Session:
                reset_attempt:{token}

            User must still satisfy the password-reset flow rules.

    The existing session token is NOT replaced or deleted.

    A new OTP overwrites the previous OTP hash for the same
    user + OTP type.
    """

    # =========================================================================
    # FIND USER
    # =========================================================================

    user = await get_user_by_email(
        db,
        data.email,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    override_email: str | None = None

    # =========================================================================
    # RESOLVE FLOW
    # =========================================================================

    if data.otp_type == "registration":

        # User must still be unverified.
        if user.verified:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account already verified",
            )

        token_key = (
            f"reg_attempt:{data.account_token}"
        )

        subject = "Verify your account"

    elif data.otp_type == "login":

        # ---------------------------------------------------------------------
        # Defense in depth.
        #
        # initiate_login() already checks these conditions.
        #
        # However, the account state may change between:
        #
        # initiate_login()
        #        ↓
        # resend_otp()
        #
        # Therefore check again.
        # ---------------------------------------------------------------------

        if user.disabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Account suspended. "
                    "Please contact admin."
                ),
            )

        if not user.verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Account not verified. "
                    "Please verify your email first."
                ),
            )

        token_key = (
            f"login_attempt:{data.account_token}"
        )

        subject = "Your login OTP"

    elif data.otp_type == "email_change":

        token_key = (
            f"email_change:{data.account_token}"
        )

        subject = "Verify your new email address"

    elif data.otp_type == "password_reset":

        token_key = (
            f"reset_attempt:{data.account_token}"
        )

        subject = "Reset your password"

    else:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OTP type for resend",
        )

    # =========================================================================
    # VALIDATE SESSION TOKEN
    # =========================================================================

    stored = await redis.get(token_key)

    if not stored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Session expired - "
                "please start over"
            ),
        )

    if isinstance(stored, bytes):
        stored = stored.decode("utf-8")

    # =========================================================================
    # EMAIL CHANGE SESSION
    # =========================================================================

    if data.otp_type == "email_change":

        try:
            session = json.loads(stored)

            stored_user_id = int(
                session["user_id"]
            )

            new_email = normalize_email(
                session["new_email"]
            )

        except (
            TypeError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ):

            await redis.delete(token_key)

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Session expired - "
                    "please start over"
                ),
            )

        # ---------------------------------------------------------------------
        # Make sure the email-change session belongs to this user.
        # ---------------------------------------------------------------------

        if (
            stored_user_id != user_id
            or not new_email
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Session expired - "
                    "please start over"
                ),
            )

        # ---------------------------------------------------------------------
        # IMPORTANT:
        #
        # Do not trust a client-supplied new email during resend.
        #
        # The Redis email-change session is the source of truth.
        # ---------------------------------------------------------------------

        override_email = new_email

    # =========================================================================
    # REGISTRATION / LOGIN / PASSWORD RESET
    # =========================================================================

    else:

        try:
            stored_user_id = int(stored)

        except (TypeError, ValueError):

            await redis.delete(token_key)

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Session expired - "
                    "please start over"
                ),
            )

        # ---------------------------------------------------------------------
        # Make sure the session belongs to this user.
        # ---------------------------------------------------------------------

        if stored_user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Session expired - "
                    "please start over"
                ),
            )

    # =========================================================================
    # GENERATE + SEND NEW OTP
    # =========================================================================

    await generate_and_send_otp(
        user=user,
        otp_type=data.otp_type,
        subject=subject,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        override_email=override_email,
    )

    return {
        "message": "A new OTP has been sent to your email" }



#grok
import hashlib
import json
import logging
import secrets
from datetime import timedelta

from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

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
        registration
        login
        email_change
        password_reset
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

        registration   → reg_attempt:{token}
            - User must still be unverified

        login          → login_attempt:{token}
            - Only created for verified + active users by initiate_login

        email_change   → email_change:{token}
            - Session holds user_id + new_email
            - OTP is sent to the new email

        password_reset → reset_attempt:{token}
            - Created by request_reset_password
            - Logged-in users are blocked at route/logic level

    The original session token is NOT deleted on resend.
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
        token_key = f"login_attempt:{data.account_token}"
        subject = "Your login OTP"

    elif data.otp_type == "email_change":
        token_key = f"email_change:{data.account_token}"
        subject = "Verify your new email address"

    elif data.otp_type == "password_reset":
        token_key = f"reset_attempt:{data.account_token}"
        subject = "Reset your password"

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
        # registration / login / password_reset → value is plain user_id
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






#chat got without password_reset 






# =============================================================================
# OTP / VERIFICATION LOGIC
# =============================================================================

import hashlib
import json
import logging
import secrets
from datetime import timedelta

from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.schemas import ResendVerificationRequest, ResendOtpRequest
from api.core.email import normalize_email
from api.core.otp_config import (
    OTP_EXPIRE_MINUTES,
    OTP_LENGTH,
    OTP_RATE_LIMIT,
    OTP_RATE_WINDOW,
)
from api.email.utils import send_otp
from api.users.models import User
from api.users.schemas import ReadUser
from api.users.utils import get_user_id
from api.users.logic import get_user_by_email

logger = logging.getLogger(__name__)


# =============================================================================
# OTP GENERATION
# =============================================================================

def generate_otp() -> str:
    """
    Generate a cryptographically secure numeric OTP.
    """
    return "".join(
        str(secrets.randbelow(10))
        for _ in range(OTP_LENGTH)
    )


# =============================================================================
# OTP HASHING
# =============================================================================

def hash_otp(otp: str) -> str:
    """
    Hash an OTP before storing it in Redis.

    The plaintext OTP is never stored in Redis.
    """
    return hashlib.sha256(
        otp.encode("utf-8")
    ).hexdigest()


# =============================================================================
# ATOMIC OTP VERIFY + CONSUME
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
    Atomically verify and consume an OTP.

    Returns:
        True  -> OTP is valid and has been consumed.
        False -> OTP is missing, expired, or invalid.

    Redis result:
        0 -> OTP does not exist / expired
        1 -> OTP matched and was deleted
        2 -> OTP exists but did not match
    """

    submitted_hash = hash_otp(submitted_otp)

    script = redis.register_script(
        VERIFY_AND_CONSUME_OTP_SCRIPT
    )

    result = await script(
        keys=[otp_key],
        args=[submitted_hash],
    )

    return result == 1


# =============================================================================
# GENERATE + STORE + SEND OTP
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
    Generate a new OTP, store only its hash in Redis, and queue the email.

    Redis key:
        otp:{user_id}:{otp_type}

    Example:
        otp:15:registration
        otp:15:login
        otp:15:email_change
        otp:15:password_reset
    """

    allowed_types = {
        "registration",
        "login",
        "email_change",
        "password_reset",
    }

    if otp_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OTP type",
        )

    user_id = get_user_id(user)

    # -------------------------------------------------------------------------
    # OTP rate limiting
    # -------------------------------------------------------------------------

    rate_key = f"otp_rate:{user_id}:{otp_type}"

    count = await redis.incr(rate_key)

    if count == 1:
        await redis.expire(
            rate_key,
            OTP_RATE_WINDOW,
        )

    if count > OTP_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Try again in 1 hour.",
        )

    # -------------------------------------------------------------------------
    # Generate OTP
    # -------------------------------------------------------------------------

    otp = generate_otp()
    otp_hash = hash_otp(otp)

    otp_key = f"otp:{user_id}:{otp_type}"

    # -------------------------------------------------------------------------
    # Store ONLY the hash
    #
    # SET automatically replaces an existing OTP for the same user/type.
    # Therefore a new OTP invalidates the previous OTP.
    # -------------------------------------------------------------------------

    await redis.set(
        otp_key,
        otp_hash,
        ex=int(
            timedelta(
                minutes=OTP_EXPIRE_MINUTES
            ).total_seconds()
        ),
    )

    recipient = (
        override_email
        if override_email is not None
        else user.email
    )

    logger.info(
        "OTP generated for user_id=%s, type=%s. "
        "Queuing email.",
        user_id,
        otp_type,
    )

    # -------------------------------------------------------------------------
    # Send OTP in background.
    #
    # Plaintext OTP exists only in application memory and is passed to
    # the email task. It is never stored in Redis.
    # -------------------------------------------------------------------------

    background_tasks.add_task(
        send_otp,
        email=recipient,
        otp=otp,
        subject=subject,
        otp_type=otp_type,
        mailer=mailer,
    )


# async def resend_verification(
    data: ResendVerificationRequest,   # simple schema: just email
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Resend verification OTP to an unverified user.

    This is used when a user tries to log in and receives
    status = "unverified". It does NOT require an old
    registration session token.
    """

    user = await get_user_by_email(db, data.email)

    # Generic response to avoid account enumeration
    generic = {
        "message": "If an unverified account exists for this email, "
                   "a new verification OTP has been sent.",
    }

    if not user:
        return generic

    if user.disabled:
        # Still return generic – don't reveal the account is disabled here
        return generic

    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is already verified. Please login.",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # Send a fresh registration OTP
    # ------------------------------------------------------------------
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
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(
            "Failed to resend verification OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        return generic
    except Exception:
        logger.exception(
            "Unexpected error while resending verification OTP for user_id=%s",
            user_id,
        )
        return generic

    # ------------------------------------------------------------------
    # Create a new anti-replay registration token
    # ------------------------------------------------------------------
    reg_token = secrets.token_urlsafe(32)

    await redis.set(
        f"reg_attempt:{reg_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info("Verification OTP resent for user_id=%s", user_id)

    return {
        "message": "A new verification OTP has been sent to your email.",
        "email": user.email,
        "reg_token": reg_token,          # frontend needs this for verify step
    }
# RESEND VERIFICATION
# =============================================================================

async def resend_verification(
    data: ResendVerificationRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Start a fresh email-verification flow for an existing unverified account.

    This endpoint is specifically for a user who:

        1. Registered previously.
        2. Never verified the account.
        3. Returns days/weeks later.
        4. Attempts to log in.
        5. Is told that the account is not verified.
        6. Requests a fresh verification OTP.

    This endpoint does NOT require the old registration token.

    New flow:

        email
          ↓
        find user
          ↓
        ensure account is still unverified
          ↓
        generate new OTP
          ↓
        store hashed OTP:
            otp:{user_id}:registration
          ↓
        create new:
            reg_attempt:{new_token}
          ↓
        send OTP
          ↓
        return new reg_token
    """

    user = await get_user_by_email(
        db,
        data.email,
    )

    # =========================================================================
    # Generic response
    #
    # Used when the account does not exist or is disabled so this endpoint
    # does not reveal account existence/status.
    # =========================================================================

    generic = {
        "message": (
            "If an unverified account exists for this email, "
            "a new verification OTP has been sent."
        ),
    }

    # -------------------------------------------------------------------------
    # User does not exist
    # -------------------------------------------------------------------------

    if not user:
        return generic

    # -------------------------------------------------------------------------
    # Disabled account
    #
    # Do not reveal that the account exists or is disabled.
    # -------------------------------------------------------------------------

    if user.disabled:
        return generic

    # -------------------------------------------------------------------------
    # Already verified
    #
    # This is intentionally different from the generic response because the
    # frontend can immediately send the user to login.
    # -------------------------------------------------------------------------

    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Account is already verified. "
                "Please login."
            ),
        )

    user_id = get_user_id(user)

    # =========================================================================
    # Create a NEW registration token
    # =========================================================================

    reg_token = secrets.token_urlsafe(32)

    reg_key = f"reg_attempt:{reg_token}"

    session_ttl = int(
        timedelta(
            minutes=OTP_EXPIRE_MINUTES
        ).total_seconds()
    )

    await redis.set(
        reg_key,
        str(user_id),
        ex=session_ttl,
    )

    # =========================================================================
    # Generate and send a NEW registration OTP
    # =========================================================================

    try:
        await generate_and_send_otp(
            user=user,
            otp_type="registration",
            subject="Verify your account",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
        )

    except HTTPException as exc:

        # ---------------------------------------------------------------------
        # Rate limit
        #
        # Remove the registration token we just created because this attempt
        # should not remain usable when no new OTP was issued.
        # ---------------------------------------------------------------------

        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            await redis.delete(reg_key)
            raise

        # ---------------------------------------------------------------------
        # Other expected HTTP error
        # ---------------------------------------------------------------------

        await redis.delete(reg_key)

        logger.error(
            "Failed to resend verification OTP for user_id=%s: %s",
            user_id,
            exc.detail,
        )

        return generic

    except Exception:
        # ---------------------------------------------------------------------
        # Unexpected failure
        # ---------------------------------------------------------------------

        await redis.delete(reg_key)

        logger.exception(
            "Unexpected error while resending verification OTP "
            "for user_id=%s",
            user_id,
        )

        return generic

    logger.info(
        "Verification OTP resent for user_id=%s",
        user_id,
    )

    # =========================================================================
    # Return the NEW registration token
    # =========================================================================

    return {
        "message": (
            "A new verification OTP has been sent to your email."
        ),
        "email": user.email,
        "reg_token": reg_token,
    }


# =============================================================================
# RESEND OTP FOR ACTIVE AUTHENTICATION FLOWS
# =============================================================================

async def resend_otp(
    data: ResendOtpRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Resend OTP for an already-started authentication flow.

    Supported flows:

        registration:
            reg_attempt:{token}

        login:
            login_attempt:{token}

        email_change:
            email_change:{token}

    NOTE:

    Old/unverified accounts returning days or weeks later should use
    resend_verification(), not this endpoint.
    """

    user = await get_user_by_email(
        db,
        data.email,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    override_email: str | None = None

    # =========================================================================
    # REGISTRATION
    # =========================================================================

    if data.otp_type == "registration":

        if user.verified:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account already verified",
            )

        token_key = (
            f"reg_attempt:{data.account_token}"
        )

        subject = "Verify your account"

    # =========================================================================
    # LOGIN
    # =========================================================================

    elif data.otp_type == "login":

        # ---------------------------------------------------------------------
        # Defense in depth.
        #
        # initial_login() already prevents OTP issuance for disabled and
        # unverified accounts. These checks protect against account state
        # changing between initial_login() and resend_otp().
        # ---------------------------------------------------------------------

        if user.disabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Account suspended. "
                    "Please contact admin."
                ),
            )

        if not user.verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Account not verified. "
                    "Please verify your email first."
                ),
            )

        token_key = (
            f"login_attempt:{data.account_token}"
        )

        subject = "Your login OTP"

    # =========================================================================
    # EMAIL CHANGE
    # =========================================================================

    elif data.otp_type == "email_change":

        token_key = (
            f"email_change:{data.account_token}"
        )

        subject = "Verify your new email address"

    # =========================================================================
    # INVALID TYPE
    # =========================================================================

    else:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OTP type for resend",
        )

    # =========================================================================
    # Retrieve authentication/session token
    # =========================================================================

    stored = await redis.get(token_key)

    if not stored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Session expired - "
                "please start over"
            ),
        )

    if isinstance(stored, bytes):
        stored = stored.decode("utf-8")

    # =========================================================================
    # EMAIL CHANGE SESSION
    # =========================================================================

    if data.otp_type == "email_change":

        try:
            session = json.loads(stored)

            stored_user_id = int(
                session["user_id"]
            )

            new_email = normalize_email(
                session["new_email"]
            )

        except (
            TypeError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ):

            await redis.delete(token_key)

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Session expired - "
                    "please start over"
                ),
            )

        # ---------------------------------------------------------------------
        # Ensure the email-change session belongs to this user.
        # ---------------------------------------------------------------------

        if (
            stored_user_id != user_id
            or not new_email
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Session expired - "
                    "please start over"
                ),
            )

        # ---------------------------------------------------------------------
        # IMPORTANT:
        #
        # Do NOT trust a new email supplied by the client during resend.
        #
        # The Redis email-change session is the source of truth.
        # ---------------------------------------------------------------------

        override_email = new_email

    # =========================================================================
    # REGISTRATION / LOGIN SESSION
    # =========================================================================

    else:

        try:
            stored_user_id = int(stored)

        except (TypeError, ValueError):

            await redis.delete(token_key)

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Session expired - "
                    "please start over"
                ),
            )

        if stored_user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Session expired - "
                    "please start over"
                ),
            )

    # =========================================================================
    # Generate and send the new OTP
    # =========================================================================

    await generate_and_send_otp(
        user=user,
        otp_type=data.otp_type,
        subject=subject,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        override_email=override_email,
    )

    return {
        "message": (
            "A new OTP has been sent to your email"
        ),
    }


# =============================================================================
# VERIFY REGISTRATION OTP
# =============================================================================

async def verify_registration_otp(
    data,
    db: AsyncSession,
    redis: Redis,
) -> ReadUser:
    """
    Verify an account registration OTP.

    Required:

        email
        otp_code
        reg_token

    Redis:

        reg_attempt:{reg_token}
            -> user_id

        otp:{user_id}:registration
            -> SHA-256 OTP hash

    The OTP is atomically verified and consumed.
    """

    # =========================================================================
    # Find user
    # =========================================================================

    user = await get_user_by_email(
        db,
        data.email,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # =========================================================================
    # Already verified
    # =========================================================================

    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account is already verified",
        )

    user_id = get_user_id(user)

    # =========================================================================
    # Validate registration session
    # =========================================================================

    reg_key = (
        f"reg_attempt:{data.reg_token}"
    )

    stored_user_id = await redis.get(
        reg_key
    )

    if not stored_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Verification session expired. "
                "Please request a new verification OTP."
            ),
        )

    if isinstance(stored_user_id, bytes):
        stored_user_id = stored_user_id.decode(
            "utf-8"
        )

    try:
        stored_user_id = int(
            stored_user_id
        )

    except (TypeError, ValueError):

        await redis.delete(reg_key)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Verification session expired. "
                "Please request a new verification OTP."
            ),
        )

    # =========================================================================
    # Ensure token belongs to this user
    # =========================================================================

    if stored_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Verification session expired. "
                "Please request a new verification OTP."
            ),
        )

    # =========================================================================
    # Verify + atomically consume OTP
    # =========================================================================

    otp_key = (
        f"otp:{user_id}:registration"
    )

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

    # =========================================================================
    # OTP is valid.
    #
    # Delete the registration session so the same registration token cannot
    # be reused.
    # =========================================================================

    await redis.delete(reg_key)

    # =========================================================================
    # Mark account as verified
    # =========================================================================

    user.verified = True

    # Use your existing verification timestamp field here.
    # Example:
    #
    # user.date_verified = datetime.utcnow()
    #
    # Keep your project's existing datetime implementation.

    await db.commit()
    await db.refresh(user)

    logger.info(
        "User account verified successfully: user_id=%s",
        user_id,
    )

    return ReadUser.model_validate(user)


