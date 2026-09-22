"""
User business logic.

Flow:
    Registration: POST /register → OTP email → POST /verify-registration
    Login:        POST /login    → OTP email → POST /verify-login
    Logout:       POST /logout   (clears cookies + revokes tokens)
"""
from typing import Literal
import secrets
import hashlib
import logging
from typing import Any
from datetime import datetime, timedelta, timezone
import json
import jwt
from fastapi import BackgroundTasks, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from redis.asyncio import Redis
from fastapi_mail import FastMail
from api.core.auth import (
    hash_password,
    revoke_all_user_tokens,
    verify_password,
    create_access_token,
    create_refresh_token,
    #decode_access_token,
    revoke_refresh_token,
    set_auth_cookies,
    clear_auth_cookies,
    generate_csrf_token,
    REFRESH_TOKEN_COOKIE,
)
from api.core.settings import get_settings
from api.models.users import User
from api.users.schemas import (
    CreateUser,
    LoginRequest,
    ReadUser,
    VerifyOtpRequest,
    VerifyPassword,
    UpdateNames,
    ResendOtpRequest,
    ConfirmPasswordReset,
    RequestResetPassword,
    ApproveEmailChangeRequest,
    ApproveEmailChangeResponse,
    RequestEmailChangeRequest,
    RequestEmailChangeResponse,
    VerifyEmailChange,
    ContactAdminMessage,
    ResendVerificationRequest,
    RequestPasswordChange,
    ConfirmPasswordChange,
)
from api.core.validators import normalize_email
from api.models.home import Country
from api.users.send_otp_email import send_otp, send_support_message
logger = logging.getLogger(__name__)


settings = get_settings()

# OTP Configuration
OTP_LENGTH = 6
OTP_EXPIRE_MINUTES = 10
OTP_RATE_LIMIT = 5
OTP_RATE_WINDOW = 3600  # 1 hour




async def get_user_by_email(
    db: AsyncSession,
    email: str,
) -> User | None:
    """Fetch user by email."""
    normalized_email = email.strip().lower()
    result = await db.execute(
        select(User).where(
            User.email == normalized_email
            )
        )
    return result.scalars().first()


async def get_user_by_id(
    db: AsyncSession,
    user_id: int,
) -> User | None:
    """Fetch user by ID."""
    return await db.get(User, user_id)

def get_user_id(user:User) -> int:
    if user.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User Id Missing"
        )
    return user.id


# EMAIL CHANGE HELPER

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
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    override_email: str | None = None,
) -> int:
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
        change_password
        password_reset
    """

    allowed = {
        "registration",
        "login",
        "email_change",
        "email_change_approval",
        "change_password",
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
            settings.otp_rate_window,
        )

    if count > settings.otp_rate_window:
        ttl= await redis.ttl(rate_key)
        retry_after = (
            ttl if isinstance(ttl,int) and ttl > 0 else settings.otp_rate_window
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Try again in 1 hour.",
            headers={"Retry-After": str(retry_after)},
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
    """
    capture primitive now while user is still attached to the request session
    Never pass the orm object into task
    """
    full_names = user.full_names
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
        full_names=full_names,
    )
    return count
    

OtpType = Literal["registration", "login", "email_change","email_change_approval" "password_reset", "change_password"]

_SESSION_EXPIRED = "Session expired - please start over"


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
            Session:  reg_attempt:{token}
            Rule:     user must still be unverified.

        login
            Session:  login_attempt:{token}
            Rule:     user must still be verified AND active.
            Note:     session is created only for verified + active users,
                      but we re-check state as defense-in-depth.
        change_password
            session: change_password_attempt
            Rule:     user must still be verified AND active.
            Note:     session is created only for verified + active users,
                      but we re-check state as defense-in-depth.
        email_change_approval
            session:email_change
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

        user_id,new_email = await parse_email_change_session(
            redis=redis,
            token=data.account_token,
            delete_on_error=True,
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
            db=db,
            background_tasks=background_tasks,
            override_email=new_email,
        )

        await _refresh_session_ttl(redis, email_change_key(data.account_token))

        logger.info(
            "Resent email_change OTP for user_id=%s (target=%s)",
            user_id,
            new_email,
        )

        return {"message": "A new OTP has been sent to your email"}

    # =========================================================================
    # REGISTRATION / LOGIN / PASSWORD_RESET/ CHANGE PASSWORD — look up by client email
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
        
    # NEW
    elif data.otp_type == "email_change_approval":
          # Defense-in-depth: state may have changed
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
        token_key = f"email_approval:{data.account_token}"
        subject = "approve your email change"
    
    elif data.otp_type == "change_password":
    
     # Defense-in-depth: state may have changed
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
        token_key = f"change_password_attempt:{data.account_token}"
        subject = "Password Change Request"
        
        

    

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
    attempts = await generate_and_send_otp(
        user=user,
        otp_type=data.otp_type,
        subject=subject,
        redis=redis,
        mailer=mailer,
        db=db,
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

    return {
        "message": "A new OTP has been sent to your email",
        "otp_attempts_used":attempts,
        "otp_expires_in_seconds": int(
            timedelta(minutes=settings.otp_expire_minutes).total_seconds()
        ),
        }


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




# RESEND OTP FOR USERS WHO HAS REGISTER BEFORE
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
        attempts = await generate_and_send_otp(
            user=user,
            otp_type="registration",
            subject="Verify your account",
            redis=redis,
            db=db,
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
        "otp_attempts_used":attempts,
        "otp_expires_in_seconds": int(
            timedelta(minutes=settings.otp_expire_minutes).total_seconds()
        ),
    }


# =============================================================================
# REGISTRATION
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
       attempts = await generate_and_send_otp(
            user=new_user,
            otp_type="registration",
            subject="Verify your account",
            redis=redis,
            db=db,
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

    otp_expires_in_seconds = int(
        timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
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
        "otp_attempts_used":attempts,
        "otp_expires_in_seconds":otp_expires_in_seconds,
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
# LOGIN
# =============================================================================
 
async def initiate_login(
    data: LoginRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Validate credentials and start login OTP.

    Only verified + active users receive an OTP and login_attempt token.

    Responses:
        status=disabled   → frontend → contact admin (no OTP)
        status=unverified → frontend → resend verification (no OTP)
        status=otp_required → frontend → OTP screen → complete_login
    """
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Already logged in",
        )

    # ------------------------------------------------------------------
    # Credentials (same error → no enumeration)
    # ------------------------------------------------------------------
    email = normalize_email(data.email)
    user = await get_user_by_email(db, email)

    if user is None or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # Account status BEFORE any OTP
    # ------------------------------------------------------------------
    if user.disabled:
        logger.warning("Disabled user login attempt: user_id=%s", user_id)
        return {
            "status": "disabled",
            "message": "Account suspended. Please contact admin.",
            "email": user.email,
        }

    if not user.verified:
        logger.info("Unverified user login attempt: user_id=%s", user_id)
        return {
            "status": "unverified",
            "message": "Account not verified. Please verify your email.",
            "email": user.email,
        }

    # ------------------------------------------------------------------
    # Rate limit (only for users who can log in)
    # ------------------------------------------------------------------
    rate_key = f"login_rate:{user_id}"
    attempts = await redis.incr(rate_key)
    if attempts == 1:
        await redis.expire(
            rate_key,
            int(timedelta(minutes=15).total_seconds()),
        )
    if attempts > 8:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes.",
        )

    # ------------------------------------------------------------------
    # Send login OTP (verified + active only)
    # ------------------------------------------------------------------
   
    try:
        attempts = await generate_and_send_otp(
            user=user,
            otp_type="login",
            subject="Your login OTP",
            redis=redis,
            mailer=mailer,
            db=db,
            background_tasks=background_tasks,
        )
    except HTTPException as e:
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(
            "Failed to send login OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error while sending login OTP for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    # ------------------------------------------------------------------
    # Anti-replay session only after OTP is stored
    # ------------------------------------------------------------------
    login_token = secrets.token_urlsafe(32)
    await redis.set(
        f"login_attempt:{login_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info("Login OTP sent to user_id=%s", user_id)
    
    otp_expires_in_seconds = int(
        timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
    )

    return {
        "status": "otp_required",
        "message": "OTP sent to your email",
        "email": user.email,
        "login_token": login_token,
        "otp_attempts_used":attempts,
        "otp_expires_in_seconds":otp_expires_in_seconds,
    }



async def complete_login(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: Redis,
    response: Response,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 2: Verify login OTP and issue cookies.

    Flow:
        1. Reject already-authenticated users.
        2. Find user.
        3. Validate login attempt session.
        4. Verify current account status.
        5. Atomically verify + consume hashed OTP.
        6. Consume login session.
        7. Clear login rate limit.
        8. Issue access/refresh/CSRF cookies.

    Only reached for users who already passed initiate_login
    (verified + active at that time). Status checks remain as a
    defense-in-depth safety net in case account state changed.
    """

    # ------------------------------------------------------------------
    # 0. Already authenticated
    # ------------------------------------------------------------------
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Already logged in",
        )

    # ------------------------------------------------------------------
    # 1. Find user
    # ------------------------------------------------------------------
    email = normalize_email(data.email)
    user = await get_user_by_email(db, email)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 2. Redis keys
    # ------------------------------------------------------------------
    login_attempt_key = f"login_attempt:{data.account_token}"
    otp_key = f"otp:{user_id}:login"

    # ------------------------------------------------------------------
    # 3. Validate login session
    #
    # Do NOT delete it yet.
    # It is consumed only after successful OTP verification.
    # ------------------------------------------------------------------
    stored_id = await redis.get(login_attempt_key)

    if not stored_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid",
        )

    if isinstance(stored_id, bytes):
        stored_id = stored_id.decode("utf-8")

    try:
        stored_user_id = int(stored_id)

    except (TypeError, ValueError):
        await redis.delete(login_attempt_key)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid",
        )

    if stored_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid",
        )

    # ------------------------------------------------------------------
    # 4. Defense-in-depth account status checks
    #
    # initiate_login() already checked these.
    # We check again because account state may have changed while
    # the OTP was waiting to be verified.
    # ------------------------------------------------------------------
    if user.disabled:
        logger.warning(
            "Disabled user attempted complete_login: user_id=%s",
            user_id,
        )
        # clean up session so usrrs cant stuck retrying
        await redis.delete(login_attempt_key)
        return{
            "status":"disabled",
            "message":"Account suspended,Please contact admin",
            "email":user.email,
        }

    if not user.verified:
        logger.info(
            "Unverified user attempted complete_login: user_id=%s",
            user_id,
        )

        await redis.delete(login_attempt_key)
        return{
            "status":"unverified",
            "message":"Account not verified,Please verify your account",
            "email": user.email
        }

    # ------------------------------------------------------------------
    # 5. Atomically verify + consume hashed OTP
    #
    # Redis key:
    #     otp:{user_id}:login
    #
    # Redis value:
    #     SHA-256 hash of the OTP
    #
    # Successful verification atomically deletes the OTP.
    # ------------------------------------------------------------------
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
    # 6. Consume login session
    # ------------------------------------------------------------------
    await redis.delete(login_attempt_key)

    # ------------------------------------------------------------------
    # 7. Clear login rate limit
    #
    # The user successfully completed login, so there is no reason
    # to retain the initiate_login OTP-request counter.
    # ------------------------------------------------------------------
    await redis.delete(f"login_rate:{user_id}")

    # ------------------------------------------------------------------
    # 8. Issue authentication tokens
    # ------------------------------------------------------------------
    access_token = create_access_token(user_id)

    refresh_token = await create_refresh_token(
        user_id,
        redis,
    )

    csrf_token = await generate_csrf_token(
        user_id,
        redis,
    )

    # ------------------------------------------------------------------
    # 9. Set authentication cookies
    # ------------------------------------------------------------------
    set_auth_cookies(
        response=response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )

    logger.info(
        "Login successful for user_id=%s",
        user_id,
    )

    return {
        "status": "success",
        "message": "Login successful",
    }
    
 

# =============================================================================
# LOGOUT
# =============================================================================
async def logout_user(
    request: Request,
    response: Response,
    redis: Redis,
    current_user: ReadUser,
) -> dict:
    """
    Logout current device.

    - Revokes the current refresh token
    - Clears auth cookies
    - Removes any leftover OTP keys for this user
    """
    refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if refresh_token:
        await revoke_refresh_token(refresh_token, redis)

    user_id = current_user.id

    # Final OTP key shape: otp:{user_id}:{otp_type}
    otp_keys = [
        f"otp:{user_id}:login",
        f"otp:{user_id}:registration",
        f"otp:{user_id}:email_change",
        f"otp:{user_id}:password_reset",
        f"otp:{user_id}:change_password",
        f"otp:{user_id}:email_change_approval",
    ]
    await redis.delete(*otp_keys)

    clear_auth_cookies(response)

    return {"message": "Logged out successfully"}



async def update_user_names(
    data: UpdateNames,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update authenticated user's surname and/or othernames.

    - Requires an authenticated user.
    - Rejects a completely empty update payload.
    - Only updates fields that actually changed.
    - Rejects the request if no actual changes were made.
    - Returns the updated user.
    """

    # ==============================================================
    # 1. Reject completely empty update payload
    # ==============================================================

    if all(
        value is None
        for value in (
            data.surname,
            data.othernames,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ==============================================================
    # 2. Get the authenticated user from the database
    # ==============================================================

    user = await get_user_by_id(
        db,
        current_user.id,
    )

    if  user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # ==============================================================
    # 3. Update only fields that actually changed
    # ==============================================================

    updated_fields: list[str] = []

    if data.surname is not None:
        if data.surname != user.surname:
            user.surname = data.surname
            updated_fields.append("surname")

    if data.othernames is not None:
        if data.othernames != user.othernames:
            user.othernames = data.othernames
            updated_fields.append("othernames")

    # ==============================================================
    # 4. Reject if nothing actually changed
    # ==============================================================

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
        
    except Exception as e:
        await db.rollback()
        logger.exception("Cannot complete username update")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="error occured while updating your name"
        )

   
    logger.info(
        f"User {current_user.id} updated: "
        f"{', '.join(updated_fields)}"
    )

    # ==============================================================
    # 7. Return updated user
    # ==============================================================

    return {
        "message":"Your name was updated successfully",
        "user":ReadUser.model_validate(user)
    }


# DELETE ACCOUNT

async def delete_account(
    data: VerifyPassword,
    db: AsyncSession,
    redis: Redis,
    response: Response,
    current_user: ReadUser,
) -> dict:
    """
    Permanently delete the authenticated user's account.

    Flow:
        1. Fetch the user from the database.
        2. Verify the provided password.
        3. Revoke ALL refresh tokens.
        4. Remove user-specific Redis authentication data.
        5. Delete the user from the database.
        6. Clear authentication cookies.

    Redis cleanup includes:
        - CSRF token
        - Login rate limit
        - OTP rate limits
        - OTP hashes for all OTP types

    Flow-specific attempt/session keys such as:

        reg_attempt:{token}
        login_attempt:{token}
        email_change:{token}
        reset_attempt:{token}

    are intentionally NOT searched or deleted here because:

        - Their keys contain random tokens rather than user IDs.
        - They have short TTLs.
        - They automatically expire.
        - Their associated user must still exist for the relevant
          endpoint to proceed.

    Raises:
        HTTPException:
            If the user does not exist, the password is invalid,
            token revocation fails, or database deletion fails.
    """

    # ------------------------------------------------------------------
    # 1. Get the user
    # ------------------------------------------------------------------
    user = await get_user_by_id(
        db,
        current_user.id,
    )

    if not user:
        logger.warning(
            "Delete account attempted for non-existent user_id=%s",
            current_user.id,
        )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    # Save email for logging before deleting the SQLAlchemy object.
    user_email = user.email

    # ------------------------------------------------------------------
    # 2. Verify current password
    # ------------------------------------------------------------------
    if not verify_password(
        data.password,
        user.hashed_password,
    ):
        logger.warning(
            "Failed password verification for account deletion: "
            "user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    # ------------------------------------------------------------------
    # 3. Revoke ALL refresh tokens
    #
    # This happens before account deletion so that existing refresh
    # tokens are invalidated immediately.
    #
    # If revocation fails, abort account deletion.
    # ------------------------------------------------------------------
    try:
        await revoke_all_user_tokens(
            user_id,
            redis,
        )

        logger.debug(
            "Revoked all refresh tokens for user_id=%s",
            user_id,
        )

    except Exception as exc:
        logger.exception(
            "Failed to revoke refresh tokens for user_id=%s "
            "during account deletion",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke tokens. Account deletion aborted.",
        ) from exc

    # ------------------------------------------------------------------
    # 4. Remove user-specific Redis authentication data
    #
    # Current OTP architecture:
    #
    #   OTP:
    #       otp:{user_id}:registration
    #       otp:{user_id}:login
    #       otp:{user_id}:email_change
    #       otp:{user_id}:email_change_approval
    #       otp:{user_id}:change_password 
    #       otp:{user_id}:password_reset
    #
    #   OTP rate limits:
    #       otp_rate:{user_id}:registration
    #       otp_rate:{user_id}:login
    #       otp_rate:{user_id}:email_change
     #       otp:{user_id}:email_change_approval
    #       otp:{user_id}:change_password 
    #       otp_rate:{user_id}:password_reset
    #
    #   Other authentication state:
    #       csrf:{user_id}
    #       login_rate:{user_id}
    #
    # OTP values contain only SHA-256 hashes.
    # ------------------------------------------------------------------
    redis_keys = [
        f"csrf:{user_id}",
        f"login_rate:{user_id}",

        # OTP rate limits
        f"otp_rate:{user_id}:registration",
        f"otp_rate:{user_id}:login",
        f"otp_rate:{user_id}:email_change",
        f"otp_rate:{user_id}:email_change_approval",
        f"otp_rate:{user_id}:change_password",
        f"otp_rate:{user_id}:password_reset",
    
        # OTP hashes
        f"otp:{user_id}:registration",
        f"otp:{user_id}:login",
        f"otp:{user_id}:email_change",
        f"otp_rate:{user_id}:change_password",
        f"otp_rate:{user_id}:email_change_approval",
        f"otp:{user_id}:password_reset",
    ]

    try:
        await redis.delete(*redis_keys)

        logger.debug(
            "Cleaned up user authentication Redis keys "
            "for user_id=%s",
            user_id,
        )

    except Exception:
        # Redis keys have TTLs, so failure here should not prevent
        # permanent database deletion.
        #
        # Refresh-token revocation was handled separately above and
        # MUST NOT be silently ignored.
        logger.exception(
            "Failed to clean up Redis authentication data "
            "for user_id=%s",
            user_id,
        )

    # ------------------------------------------------------------------
    # 5. Delete user from database
    #
    # This is the permanent account deletion.
    # ------------------------------------------------------------------
    try:
        await db.delete(user)
        await db.commit()

    except Exception as exc:
        await db.rollback()

        logger.exception(
            "Failed to permanently delete user_id=%s "
            "from database",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete account. Please try again later.",
        ) from exc

    # ------------------------------------------------------------------
    # 6. Clear authentication cookies
    # ------------------------------------------------------------------
    clear_auth_cookies(response)

    # ------------------------------------------------------------------
    # 7. Security/audit log
    # ------------------------------------------------------------------
    logger.info(
        "Account deletion completed successfully: "
        "user_id=%s, email=%s",
        user_id,
        user_email,
    )

    return {
        "message": "Account deleted successfully",
    }



# __________RESET PASSWORD ____________


async def request_reset_password(
    data: RequestResetPassword,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Request a password reset OTP.

    Flow:
        1. Block already-authenticated users.
        2. Look up the account by email.
        3. Silently ignore unknown/disabled accounts.
        4. Generate and store a new password-reset OTP.
        5. Queue the OTP email.
        6. Create a reset session token.
        7. Return the reset session token.

    Redis:
        OTP:
            otp:{user_id}:password_reset

        OTP rate limit:
            otp_rate:{user_id}:password_reset

        Reset session:
            reset_attempt:{token}

    Security:
        - Prevents logged-in users from using password reset.
        - Uses a generic response for unknown/disabled accounts.
        - OTP is stored only as a SHA-256 hash.
        - A new OTP overwrites the previous OTP.
        - Reset session is created only after OTP generation/storage succeeds.
        - Reset session token is cryptographically random.
    """

    # ------------------------------------------------------------------
    # 1. Block already-authenticated users
    # ------------------------------------------------------------------
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Logged in users cannot use password reset. "
                "Use 'change password' instead."
            ),
        )

    # ------------------------------------------------------------------
    # 2. Generic response
    #
    # Used for unknown and disabled accounts to reduce email
    # enumeration.
    # ------------------------------------------------------------------
    generic_response = {
        "message": "If this email is registered, an OTP has been sent.",
    }

    # ------------------------------------------------------------------
    # 3. Look up user
    # ------------------------------------------------------------------
    user = await get_user_by_email(
        db,
        data.email,
    )

    if user is None:
        logger.info(
            "Password reset requested for unknown email"
        )
        return generic_response

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 4. Do not issue reset OTPs to disabled accounts
    # ------------------------------------------------------------------
    if user.disabled:
        logger.warning(
            "Password reset requested for disabled user_id=%s",
            user_id,
        )
        return generic_response

    # ------------------------------------------------------------------
    # 5. Generate + store password-reset OTP
    #
    # generate_and_send_otp():
    #
    #   - generates a new 6-digit OTP
    #   - hashes it with SHA-256
    #   - stores the hash at:
    #
    #       otp:{user_id}:password_reset
    #
    #   - applies OTP rate limiting
    #   - queues the email through BackgroundTasks
    #
    # A new OTP overwrites any previous OTP.
    # ------------------------------------------------------------------
    try:
        attempts = await generate_and_send_otp(
            user=user,
            otp_type="password_reset",
            subject="Reset your password",
            redis=redis,
            mailer=mailer,
            db=db,
            background_tasks=background_tasks,
        )

    except HTTPException as exc:
        # Keep the existing OTP rate-limit behavior.
        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise

        logger.error(
            "Password reset OTP generation failed "
            "for user_id=%s: %s",
            user_id,
            exc.detail,
        )

        return generic_response

    except Exception:
        logger.exception(
            "Unexpected password reset OTP generation failure "
            "for user_id=%s",
            user_id,
        )

        return generic_response

    # ------------------------------------------------------------------
    # 6. Create reset session ONLY after OTP generation succeeds
    #
    # The session value contains the user ID.
    # The token itself is cryptographically random.
    # ------------------------------------------------------------------
    reset_token = secrets.token_urlsafe(32)

    reset_key = f"reset_attempt:{reset_token}"

    reset_ttl = int(
        timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
    )

    await redis.set(
        reset_key,
        str(user_id),
        ex=reset_ttl,
    )
    
    otp_expires_in_seconds = int(
        timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
    )

    logger.info(
        "Password reset OTP generated and reset session created "
        "for user_id=%s",
        user_id,
    )

    # ------------------------------------------------------------------
    # 7. Return reset session token
    # ------------------------------------------------------------------
    return {
        "message": "If this email is registered, an OTP has been sent.",
        "reset_token": reset_token,
        "otp_attempts_used":attempts,
        "otp_expires_in_seconds": otp_expires_in_seconds
    }


async def reset_password(
    data: ConfirmPasswordReset,
    db: AsyncSession,
    redis: Redis,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 2: Verify the password-reset OTP and set a new password.

    Flow:
        1. Block already-authenticated users.
        2. Look up the user by email.
        3. Validate the reset session token.
        4. Re-check account status.
        5. Ensure the new password differs from the current password.
        6. Atomically verify + consume the OTP.
        7. Update the password.
        8. Commit the password change.
        9. Delete the reset session.
        10. Revoke ALL refresh tokens.
        11. Return success.

    Redis:
        OTP:
            otp:{user_id}:password_reset

        Reset session:
            reset_attempt:{token}

    Security:
        - Requires email + reset token + OTP.
        - OTP is stored as a SHA-256 hash.
        - OTP verification and deletion happen atomically.
        - OTP cannot be reused after successful verification.
        - Reset session expires automatically.
        - All existing refresh tokens are revoked after a successful
          password change.
        - User must authenticate again with the new password.
    """

    # ------------------------------------------------------------------
    # 1. Block already-authenticated users
    # ------------------------------------------------------------------
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Logged in users cannot use password reset. "
                "Use 'change password' instead."
            ),
        )

    # ------------------------------------------------------------------
    # 2. Look up user
    # ------------------------------------------------------------------
    user = await get_user_by_email(
        db,
        data.email,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 3. Validate reset session
    #
    # Redis:
    #
    #     reset_attempt:{token} → user_id
    # ------------------------------------------------------------------
    reset_key = f"reset_attempt:{data.reset_token}"

    stored_id = await redis.get(reset_key)

    if not stored_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Reset session expired or invalid. "
                "Please request a new OTP."
            ),
        )

    if isinstance(stored_id, bytes):
        stored_id = stored_id.decode("utf-8")

    try:
        stored_user_id = int(stored_id)

    except (TypeError, ValueError):
        # Corrupt session data should not remain in Redis.
        await redis.delete(reset_key)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Reset session expired or invalid. "
                "Please request a new OTP."
            ),
        )

    # ------------------------------------------------------------------
    # 4. Ensure reset token belongs to this user
    # ------------------------------------------------------------------
    if stored_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Reset session expired or invalid. "
                "Please request a new OTP."
            ),
        )

    # ------------------------------------------------------------------
    # 5. Defense-in-depth account status check
    #
    # The account may have been disabled after the reset request.
    # Do not allow the password reset to proceed in that situation.
    # ------------------------------------------------------------------
    if user.disabled:
        logger.warning(
            "Disabled user attempted password reset: user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
        )

    # ------------------------------------------------------------------
    # 6. Ensure new password differs from current password
    #
    # This check happens before consuming the OTP so that an otherwise
    # valid reset is not unnecessarily destroyed because the user
    # submitted their existing password.
    # ------------------------------------------------------------------
    if verify_password(
        data.new_password,
        user.hashed_password,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "New password must be different from "
                "your current password"
            ),
        )

    # ------------------------------------------------------------------
    # 7. Atomically verify + consume OTP
    #
    # Current OTP key:
    #
    #     otp:{user_id}:password_reset
    #
    # Redis contains ONLY the SHA-256 hash of the OTP.
    #
    # verify_and_consume_otp() performs:
    #
    #     GET hash
    #     compare submitted OTP hash
    #     DELETE OTP
    #
    # atomically through the Redis Lua script.
    # ------------------------------------------------------------------
    otp_key = f"otp:{user_id}:password_reset"

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
    # 8. Update password
    # ------------------------------------------------------------------
    user.hashed_password = hash_password(
        data.new_password,
    )

    db.add(user)

    # ------------------------------------------------------------------
    # 9. Commit password change
    #
    # The OTP has already been consumed.
    #
    # This is intentional:
    # a successful OTP must never be reusable, even if a later
    # database operation fails.
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        await db.rollback()

        logger.exception(
            "Password reset database commit failed "
            "for user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to reset password. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 10. Consume reset session
    #
    # OTP has already been consumed atomically.
    # The reset session is now also no longer needed.
    # ------------------------------------------------------------------
    try:
        await redis.delete(reset_key)

    except Exception:
        # Password change succeeded, so do not report the operation
        # as failed merely because deleting an already-short-lived
        # session key failed.
        logger.exception(
            "Failed to delete password reset session "
            "for user_id=%s",
            user_id,
        )

    # ------------------------------------------------------------------
    # 11. Revoke ALL existing refresh tokens
    #
    # Password reset invalidates every existing authenticated session.
    #
    # The user must login again using the new password.
    # ------------------------------------------------------------------
    try:
        await revoke_all_user_tokens(
            user_id,
            redis,
        )

    except Exception as exc:
        # The password has already changed successfully.
        # Failure here is a serious security event because existing
        # refresh tokens may still exist.
        logger.critical(
            "CRITICAL: Password reset succeeded but "
            "refresh-token revocation failed for user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Password was changed, but active sessions could "
                "not be fully revoked. Please contact support."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 12. Success
    # ------------------------------------------------------------------
    logger.info(
        "Password reset successful for user_id=%s. "
        "All refresh tokens invalidated.",
        user_id,
    )

    return {
        "message": (
            "Password reset successful. "
            "Please login with your new password."
        ),
    }


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
    
    

    Session:
        change_password_attempt:{token}
            
            
     Redis:
        OTP:
            otp:{user_id}:change_password

        OTP rate limit:
            otp_rate:{user_id}:change_password

       

            
                
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
        attempts = await generate_and_send_otp(
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
    otp_expires_in_seconds = int(
        timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
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
        "otp_attempts_used":attempts,
        "otp_expires_in_seconds":otp_expires_in_seconds
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

# CHANGE EMAIL 
async def approve_email_change(
    data: ApproveEmailChangeRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser,
) -> dict:
    """
    Stage 1: Verify current password, send OTP to the CURRENT email,
    and issue an approval session token.

    Flow:
        1. Load ORM user.
        2. Verify current password.
        3. Reject if disabled / unverified.
        4. Send OTP to user.email  (otp_type="email_change_approval").
        5. Create email_approval:{token} → user_id.

    On OTP send failure the session token is NOT created.
    """
    # 1. Load user
    user = await get_user_by_id(db, current_user.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    # 2. Verify current password
    if not verify_password(data.current_password, user.hashed_password):
        logger.warning(
            "Failed password verification for email change approval: user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    # 3. Account-state guards
    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
        )
    if not user.verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified. Please verify your email first.",
        )

    # 4. Send OTP to CURRENT email
    try:
        attempts = await generate_and_send_otp(
            user=user,
            otp_type="email_change_approval",
            subject="Approve your email change",
            redis=redis,
            mailer=mailer,
            db=db,
            background_tasks=background_tasks,
        )
    except HTTPException as e:
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(
            "Failed to send email_change_approval OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error sending email_change_approval OTP for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    # 5. Session token (only after OTP stored)
    token = secrets.token_urlsafe(32)
    await redis.set(
        f"email_approval:{token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info(
        "Email change approval OTP sent for user_id=%s", user_id
    )

    return {
        "message": (
            "An OTP to approve your change of email "
            "has been sent to your inbox."
        ),
        "email_approval_token": token,
        "otp_attempts_used": attempts,
        "otp_expires_in_seconds": int(
            timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
        ),
    }


async def request_email_change(
    data: RequestEmailChangeRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser,
) -> dict:
    """
    Stage 2: Consume the approval OTP and submit the new email.

    Flow:
        1. Load ORM user.
        2. Validate email_approval session (belongs to caller).
        3. Re-check account state (disabled / verified).
        4. Validate the new email:
             - normalize
             - must differ from current email
             - must not be already registered
           (Done BEFORE consuming the OTP so a bad email doesn't
            cost the user their approval code.)
        5. Atomically verify + consume otp:{user_id}:email_change_approval.
        6. Consume email_approval session.
        7. Send a fresh OTP to the NEW email (otp_type="email_change").
        8. Create email_change:{token} → { user_id, new_email }.
        9. Return new-email token + OTP meta.
    """

    # 1. Load user
    user = await get_user_by_id(db, current_user.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    user_id = get_user_id(user)

    # 2. Validate email_approval session
    approval_key = f"email_approval:{data.email_approval_token}"
    stored = await redis.get(approval_key)

    if not stored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approval session expired or invalid. Please start over.",
        )

    if isinstance(stored, bytes):
        stored = stored.decode("utf-8")

    try:
        stored_user_id = int(stored)
    except (TypeError, ValueError):
        await redis.delete(approval_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approval session expired or invalid. Please start over.",
        )

    if stored_user_id != user_id:
        logger.warning(
            "email_approval token mismatch: token_owner=%s, requester=%s",
            stored_user_id,
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approval session expired or invalid. Please start over.",
        )

    # 3. Account-state re-check (may have flipped since stage 1)
    if user.disabled:
        await redis.delete(approval_key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
        )
    if not user.verified:
        await redis.delete(approval_key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified. Please verify your email first.",
        )

    # 4. Validate new email FIRST (so a bad email doesn't consume the OTP)
    new_email = normalize_email(data.new_email)
    if not new_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid new email address is required",
        )

    if new_email == user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New email must be different from your current email",
        )

    existing = await get_user_by_email(db, new_email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This email address is already registered "
                "to another account"
            ),
        )

    # 5. Atomically verify + consume approval OTP
    approval_otp_key = f"otp:{user_id}:email_change_approval"
    otp_valid = await verify_and_consume_otp(
        redis=redis,
        otp_key=approval_otp_key,
        submitted_otp=data.otp_code,
    )
    if not otp_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    # 6. Consume the approval session — its purpose is fulfilled
    try:
        await redis.delete(approval_key)
    except Exception:
        logger.exception(
            "Failed to delete email_approval session for user_id=%s",
            user_id,
        )

    # 7. Send OTP to the NEW email
    try:
        attempts = await generate_and_send_otp(
            user=user,
            otp_type="email_change",
            subject="Verify your new email address",
            redis=redis,
            mailer=mailer,
            db=db,
            background_tasks=background_tasks,
            override_email=new_email,
        )
    except HTTPException as e:
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(
            "Failed to send email_change OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error sending email_change OTP for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    # 8. Create email_change session
    change_token = secrets.token_urlsafe(32)
    session_data = json.dumps(
        {"user_id": user_id, "new_email": new_email}
    )
    await redis.set(
        f"email_change:{change_token}",
        session_data,
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info(
        "Email change OTP sent to new email for user_id=%s (target=%s)",
        user_id,
        new_email,
    )

    return {
        "message": (
            "An OTP has been sent to your new email address. "
            "Please verify it to complete the email change."
        ),
        "email_change_token": change_token,
        "otp_attempts_used": attempts,
        "otp_expires_in_seconds": int(
            timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
        ),
        }






async def verify_new_email(
    data: VerifyEmailChange,
    db: AsyncSession,
    redis: Redis,
    current_user: ReadUser,
) -> ReadUser:
    """
    Step 2: Verify the OTP sent to the new email and update the account.

    Flow:
        1. Require authentication.
        2. Fetch the real ORM user.
        3. Validate the email-change session token.
        4. Parse user_id + new_email from Redis.
        5. Ensure the token belongs to the authenticated user.
        6. Re-check account status.
        7. Atomically verify + consume the OTP.
        8. Perform a final email uniqueness check.
        9. Update the email in the database.
        10. Commit the database transaction.
        11. Delete the email-change session.
        12. Revoke all refresh tokens.
        13. Return the updated user.

    Redis:

        Session:
            email_change:{token}

        OTP:
            otp:{user_id}:email_change

    Security:
        - Requires both the authenticated session and email-change token.
        - The new email is retrieved from trusted Redis state.
        - The OTP is stored as a SHA-256 hash.
        - OTP verification and deletion are atomic.
        - OTP cannot be reused after successful verification.
        - A final uniqueness check protects against stale state/races.
        - All refresh sessions are revoked after an email change.
    """

    # ------------------------------------------------------------------
    # 1. Fetch the real ORM user
    # ------------------------------------------------------------------
    user = await get_user_by_id(
        db,
        current_user.id,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 2. Validate email-change session
    # ------------------------------------------------------------------
    
    stored_user_id, new_email = await parse_email_change_session(redis=redis,token=data.email_change_token,delete_on_error=True)
   
    # ------------------------------------------------------------------
    # 4. Ensure token belongs to current user
    # ------------------------------------------------------------------
    if stored_user_id != user_id:
        logger.warning(
            "Email-change token mismatch: "
            "token_owner=%s, requester=%s",
            stored_user_id,
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This email change token does not belong "
                "to your account"
            ),
        )

    # ------------------------------------------------------------------
    # 5. Defense-in-depth account status check
    # ------------------------------------------------------------------
    if user.disabled:
        logger.warning(
            "Disabled user attempted email change: "
            "user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
        )

    # ------------------------------------------------------------------
    # 6. Make sure the new email is still different
    # ------------------------------------------------------------------
    if new_email == user.email:
        # The requested email has somehow become the current email.
        # The session is no longer useful.
        await redis.delete(email_change_key(data.email_change_token))

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This email address is already your current "
                "email address."
            ),
        )

    # ------------------------------------------------------------------
    # 7. Atomically verify + consume OTP
    #
    # Current OTP key:
    #
    #     otp:{user_id}:email_change
    #
    # Redis contains only the SHA-256 hash of the OTP.
    # ------------------------------------------------------------------
    otp_key = f"otp:{user_id}:email_change"

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
    # 8. Final uniqueness check
    #
    # Another account could have registered this email after step 1.
    #
    # IMPORTANT:
    # The database UNIQUE constraint remains the ultimate protection
    # against concurrent registration/update races.
    # ------------------------------------------------------------------
    existing = await get_user_by_email(
        db,
        new_email,
    )

    if existing is not None and get_user_id(existing) != user_id:
        # OTP has already been consumed.
        # This is intentional: a successfully verified OTP should
        # never become reusable.
        await redis.delete(email_change_key(data.email_change_token))

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This email address has just been registered "
                "by another account. Please choose a different email."
            ),
        )

    # ------------------------------------------------------------------
    # 9. Update email
    # ------------------------------------------------------------------
    old_email = user.email

    user.email = new_email

    db.add(user)

    # ------------------------------------------------------------------
    # 10. Commit database update
    # ------------------------------------------------------------------
    try:
        await db.commit()
        await db.refresh(user)

    except Exception as exc:
        await db.rollback()

        logger.exception(
            "Failed to update email for user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Unable to change email address. "
                "Please try again."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 11. Delete email-change session
    # ------------------------------------------------------------------
    try:
        await redis.delete(email_change_key(data.email_change_token))

    except Exception:
        # The database change already succeeded.
        # The session has a TTL and should not be allowed to prevent
        # the successful operation from being reported as successful.
        logger.exception(
            "Failed to delete email-change session "
            "for user_id=%s",
            user_id,
        )

    # ------------------------------------------------------------------
    # 12. Revoke all refresh tokens
    #
    # Email changes are account-security changes, so all existing
    # authenticated refresh sessions should be invalidated.
    #
    # The user must authenticate again using the account's credentials.
    # ------------------------------------------------------------------
    try:
        await revoke_all_user_tokens(
            user_id,
            redis,
        )

    except Exception as exc:
        # The email has already been changed successfully.
        # Failure to revoke sessions is therefore a serious security
        # event and must not be silently ignored.
        logger.critical(
            "CRITICAL: Email change succeeded but "
            "refresh-token revocation failed for user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Email address was changed, but active sessions "
                "could not be fully revoked. Please contact support."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 13. Success
    # ------------------------------------------------------------------
    logger.info(
        "Email changed successfully for user_id=%s: %s -> %s",
        user_id,
        old_email,
        new_email,
    )

    return ReadUser.model_validate(user)

# ===============================================================================
# CONTACT ADMIN
#================================================================================
async def handle_disabled_account(
    data: ContactAdminMessage,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Handle contact form from disabled users.
    """

    user = await get_user_by_email(db, data.email)

    # Generic response – don't reveal account status
    if not user or not user.disabled:
        return {
            "message": "If your account exists, "
                       "your message has been sent to our support team."
        }

    user_id = get_user_id(user)

    # Rate limit – max 3 per hour
    rate_key = f"contact_admin_rate:{user_id}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, 3600)
    if count > 3:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again in 1 hour.",
            headers={"Retry-After":"3600"}
        )

    # Extract Primitive from ORM objects
    full_names = user.full_names
    # Resolve support email (country → fallback)
    support_email = settings.mail_username

    if user.country_id:
        country = await db.get(Country, user.country_id)
        if country and country.email_support:
            support_email = country.email_support

    background_tasks.add_task(
        send_support_message,
        user_full_names=full_names,
        support_email=support_email,
        user_email=data.email,
        message=data.message,
        mailer=mailer,
    )

    logger.info(
        "Disabled user %s (id=%s) contacted support → %s",
        data.email,
        user_id,
        support_email,
    )

    return {
        "message": "Your message has been sent to our support team. "
                   "We will review your account and get back to you."
    }




#================================================================
# USER PROFILE
#=================================================================
async def get_user_profile(
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Load full user + relationships, then build profile dict
    with role-based fields.
    """
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.country),  # type: ignore[arg-type]
            selectinload(User.group),    # type: ignore[arg-type]
        )
        .where(User.id == current_user.id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    profile: dict = {
        "id": user.id,
        "email": user.email,
        "surname": user.surname,
        "othernames": user.othernames,
        "country": user.country.name if user.country else None,
        "verified": user.verified,
        "disabled": user.disabled,
        "date_verified": user.date_verified,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "full_names": user.full_names,
        "avatar": user.avatar
    }

    # Group info (only if user belongs to a group)
    if user.group:
        profile["name"] = user.group.name
        profile["permission"] = user.group.permission

    # Admin-only field
    if user.is_admin:
        profile["is_admin"] = True

    return profile


# =============================================================================
# PERMISSIONS
# =============================================================================

async def has_permission(
    user: ReadUser,
    required_perm: str,
    target_country_id: int | None = None,
) -> None:
    """
    Pure in-memory permission check.
    No DB queries - permission already loaded in get_current_user.
    
    Hierarchy:
        1. Admin → all permissions ✅
        2. User with matching permission → allowed ✅
        3. Everyone else → 403 ❌
    
    Args:
        user: ReadUser with .permission already set
        required_perm: Required permission string
    
    Raises:
        HTTPException: 403 if permission denied
    """
    # Admins bypass all permission checks
    if user.is_admin:
        return

    # No permission
    if user.permission is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access deny,you dont have permission",
            headers={"X-Error-Code":"no_permission"},
        )
    # wrong permission
    if user.permission != required_perm:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied - requires '{required_perm}' permission",
            headers={"X-Error-Code":"wrong_permission"},
        )
    # Country scope check
    if target_country_id is not None:
        if user.country_id is None:
            raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied no country with such permission",
            headers={"X-Error-Code":"no_country_scope"},
        )
        if user.country_id != target_country_id:
            raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You dont have permission on this country resource,",
            headers={"X-Error-Code":"wrong_country_scope"},
            )
            
            
            
 # ROUTES

from typing import Callable
from redis.asyncio import Redis
from fastapi_mail import FastMail
import jwt
from api.users.deps import CurrentUser, OptionalCurrentUser, require_admin,require_csrf,has_permission
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from regex import D
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import DBDep
from api.core.redis import RedisDep
from api.core.mail import MailDep
from api.core.auth import (
    validate_refresh_token,
    create_token_response,
    set_auth_cookies,
    REFRESH_TOKEN_COOKIE,
    
)
from api.core.settings import get_settings
from api.users.logics import (
    register_user,
    verify_registration_otp,
    initiate_login,
    complete_login,
    logout_user,
    delete_account,
    update_user_names,
    request_email_change,
    verify_new_email,
    request_reset_password,
    resend_otp,
    reset_password,
    handle_disabled_account,
    resend_verification,
    get_user_profile,
    request_password_change,
    confirm_password_change,
    approve_email_change,
    
    
    
)
from api.users.schemas import (
    CreateUser,
    LoginRequest,
    LoginResponse,
    ConfirmPasswordReset,
    RegisterResponse,
    RequestPasswordChangeResponse,
    RequestPasswordResetResponse,
    ResendOtpResponse,
    VerifyOtpRequest,
    UpdateNames,
    RequestPasswordChange,
    VerifyPassword,
    ReadUser,
    VerifyEmailChange,
    RequestResetPassword,
    ResendOtpRequest,
    ResendVerificationRequest,
    ContactAdminMessage,
    UserProfile,
    VerifyRegistrationResponse,
    MessageResponse,
    UpdateNamesResponse,
    ContactAdminResponse,
    ConfirmPasswordChange,
    ApproveEmailChangeResponse,
    ApproveEmailChangeRequest,
    RequestEmailChangeRequest,
    RequestEmailChangeResponse
)


settings = get_settings()

router = APIRouter(prefix="/auth", tags=["Authentication"])





# =============================================================================
# REGISTRATION
# =============================================================================
@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register new user account",
    response_description="OTP sent to email",
)
async def register(
    data: CreateUser,
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,   # ✅ No Depends() needed
    db: DBDep,
    current_user: OptionalCurrentUser,
) -> dict:
    """
    Step 1: Register a new user.
    
    - Validates email uniqueness
    - Validates country exists
    - Creates unverified account
    - Sends OTP to email
    
    Returns registration token for OTP verification step.
    """
    return await register_user(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user
    )


@router.post(
    "/register/verify",
    status_code=status.HTTP_200_OK,
    response_model=VerifyRegistrationResponse,
    summary="Verify registration OTP",
)
async def verify_registration(
    data: VerifyOtpRequest,
    redis: RedisDep,
    db: DBDep,

):
    """
    Step 2: Verify registration OTP.
    
    - Validates session token (anti-replay)
    - Validates OTP
    - Activates account
    
    Returns activated user profile.
    """
    return await verify_registration_otp(
        data=data,
        db=db,
        redis=redis,
    )


# =============================================================================
# LOGIN (2FA)
# =============================================================================

@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Login step 1 - validate credentials",
    response_description="OTP sent to email",
)
async def login(
    data: LoginRequest,
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,   # ✅ No Depends() needed
    db: DBDep,
    current_user:OptionalCurrentUser
) -> dict:
    """
    Step 1: Validate credentials and send OTP.
    
    - Validates email + password
    - Rate limits attempts (max 8 per 15 minutes)
    - Sends OTP via email
    
    Returns login token for OTP verification step.
    """
    return await initiate_login(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/login_verify",
    status_code=status.HTTP_200_OK,
    summary="Login step 2 - verify OTP",
    response_description="Auth cookies set",
)
async def verify_login(
    redis: RedisDep,
    data: VerifyOtpRequest,
    response: Response,                  # ✅ FastAPI injects this
    db: DBDep,
    current_user:OptionalCurrentUser
    
) -> dict:
    """
    Step 2: Verify OTP and issue tokens.
    
    - Validates session token (anti-replay)
    - Validates OTP
    - Issues tokens as HttpOnly cookies
    
    ✅ Tokens NEVER in response body - cookies only!
    """
    return await complete_login(
        data=data,
        db=db,
        redis=redis,
        response=response,    # ← Logic sets cookies via response
        current_user=current_user,
    )


# =============================================================================
# TOKEN MANAGEMENT
# =============================================================================
#final
@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    summary="Rotate tokens",
    response_description="New auth cookies set",
)
async def refresh_tokens(
    request: Request,
    response: Response,
    redis: RedisDep,
) -> dict:
    """
    Rotate access + refresh tokens.
    """
    # Get refresh token from HttpOnly cookie
    refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active session"
        )
    
    # Validate and get user_id
    user_id = await validate_refresh_token(
        refresh_token=refresh_token,
        redis=redis,
    )
    
   
    
    # Issue new tokens
    tokens = await create_token_response(
        user_id=user_id,
        redis=redis,
    )
    
    # ✅ Set as cookies only - never in body
    set_auth_cookies(
        response=response,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        csrf_token=tokens.csrf_token,
    )
    
    return {"message": "Tokens refreshed"}


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Logout user",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF protection on logout
)
async def logout(
    request: Request,
    response: Response,
    redis: RedisDep,
    current_user: CurrentUser,
) -> dict:
    """
    Logout current user.
    
    - Validates CSRF token
    - Revokes refresh token
    - Clears all auth cookies
    - Cleans up Redis data
    """
    return await logout_user(
        request=request,
        response=response,
        redis=redis,
        current_user=current_user,
    )


# =============================================================================
# PROFILE
# =============================================================================

@router.get(
    "/profile",
    response_model=ReadUser,
    status_code=status.HTTP_200_OK,
    summary="Get current user profile",
)
async def user_profile(
    current_user: CurrentUser,
) -> ReadUser:
    """Get authenticated user's profile."""
    return current_user


@router.patch(
    "/update_names",
    response_model=UpdateNamesResponse,
    status_code=status.HTTP_200_OK,
    summary="Update user names",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF on mutations
)
async def update_names(
    data: UpdateNames,
    db: DBDep,
    current_user: CurrentUser,
):
    """Update user's surname and/or othernames."""
    return await update_user_names(
        data=data,
        db=db,
        current_user=current_user,
    )


@router.delete(
    "/delete_account",
    status_code=status.HTTP_200_OK,
    summary="Delete user account",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF on destructive actions
)
async def delete_user_account(
    data: VerifyPassword,
    response: Response,
    redis: RedisDep,
    db: DBDep,
    current_user: CurrentUser,
) -> dict:
    """
    Permanently delete user account.
    
    - Requires password confirmation
    - Cleans up all user data in Redis
    - Clears auth cookies
    """
    return await delete_account(
        data=data,
        db=db,
        redis=redis,
        response=response,
        current_user=current_user,
    )


#_____ OTP SEND AND RESEND _________

@router.post(
    "/otp/resend",
    response_model=ResendOtpResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend OTP for registration or login",
)
async def resend_otp_route(
    data: ResendOtpRequest,
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,
    db: DBDep,
) -> dict:
    """
    Resend OTP if the original email didn't arrive.
    
    Rate limited to 5 requests per hour per user per flow type.
    """
    return await resend_otp(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )


@router.post(
    "/password/reset-request",
    response_model=RequestPasswordChangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Request password reset OTP",
)
async def request_password_reset(
    data: RequestResetPassword,
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,
    db: DBDep,
    # ✅ Optional - not logged in is expected here
    current_user: OptionalCurrentUser,
) -> dict:
    """
    Request a password reset OTP.

    Blocked if already authenticated (use 'change password' instead).
    Returns the same response whether email exists or not (prevents enumeration).
    """
    return await request_reset_password(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/password/reset",
    response_model=RequestPasswordResetResponse,
    status_code=status.HTTP_200_OK,
    summary="Complete password reset with OTP",
)
async def complete_password_reset(
    data: ConfirmPasswordReset,
    redis: RedisDep,
    db: DBDep,
   
    # ✅ Optional - not logged in is expected here
    current_user: OptionalCurrentUser,
) -> dict:
    """
    Complete password reset.

    Requires OTP from reset-request step and the reset_token
    returned in that response (anti-replay protection).

    Invalidates all existing sessions on success (forces re-login).
    """
    return await reset_password(
        data=data,
        db=db,
        redis=redis,
        current_user=current_user,
)


# Change Email


@router.post(
    "/me/email/approve",
    response_model=ApproveEmailChangeResponse,
    status_code=status.HTTP_200_OK,
)
async def approve_email_change_route(
    data: ApproveEmailChangeRequest,
    background_tasks: BackgroundTasks,
    db: DBDep,
    redis: RedisDep,
    mailer:MailDep,
    current_user: CurrentUser,
):
    return await approve_email_change(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/me/email/request",
    response_model=RequestEmailChangeResponse,
    status_code=status.HTTP_200_OK,
)
async def request_email_change_route(
    data: RequestEmailChangeRequest,
    background_tasks: BackgroundTasks,
    db:DBDep,
    redis: RedisDep,
    mailer:MailDep,
    current_user: CurrentUser,
):
    return await request_email_change(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/me/email/verify",
    response_model=ReadUser,
    status_code=status.HTTP_200_OK,
)
async def verify_new_email_route(
    data: VerifyEmailChange,
    db: DBDep,
    redis: RedisDep,
    current_user: CurrentUser,
):
    return await verify_new_email(
        data=data,
        db=db,
        redis=redis,
        current_user=current_user,
    )


# CONTACT ADMIN

@router.post(
    "/contact-admin",
    response_model=ContactAdminResponse,
    status_code=status.HTTP_200_OK,
    summary="Contact support (disabled accounts only)",
)
async def contact_admin_route(
    data: ContactAdminMessage,
    background_tasks: BackgroundTasks,
    db: DBDep,
    redis: RedisDep,
    mailer: MailDep,
) -> dict:
    """
    Disabled users can send a message to support.
    Message goes to country.email_support or settings.mail_username.
    Rate limited to 3 requests per hour.
    """
    return await handle_disabled_account(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )
    
    
    #================================================
    # RESEND OTP FOR UNVERIFIED USERS
    
@router.post(
    "/resend-verification",
    response_model=RegisterResponse, #added this since its same response with registration
    status_code=status.HTTP_200_OK,
    summary="Resend verification OTP (unverified accounts)",
)
async def resend_verification_route(
    data: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    db: DBDep,
    redis: RedisDep,
    mailer: MailDep,
) -> dict:
    """
    Resend verification OTP to unverified users.
    Requires account_token from login response.
    """
    return await resend_verification(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )


#==============================================================
# PROFILE

@router.get(
    "/userprofile",
    status_code=status.HTTP_200_OK,
    summary="Get current user profile",
    response_model=UserProfile,
    response_model_exclude_none=True,   # ✅ Hides None fields from response
)
async def get_profile(
    db: DBDep,
    current_user: CurrentUser
) -> dict:
    """
    Get authenticated user profile.

    Response varies by role:
        Regular user: base fields + group info if assigned
        Admin: base fields + group info + is_admin: true
    """
    return await get_user_profile(
        db=db,
        current_user=current_user,
    )

@router.post(
    "/password/request",
    response_model=RequestPasswordChangeResponse,
    status_code=status.HTTP_200_OK,
)
async def request_password_change_route(
    data: RequestPasswordChange,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    db: DBDep,
    redis: RedisDep,
    mailer:MailDep,
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
    db: DBDep,
    redis: RedisDep,
    current_user: CurrentUser,
):
    return await confirm_password_change(
        data=data,
        db=db,
        redis=redis,
        current_user=current_user,
    )








