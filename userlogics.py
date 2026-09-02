#new 
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

# Adjust imports to your project structure
# from api.users.models import User, Country
# from api.users.schemas import ReadUser, CreateUser
# from api.auth.schemas import VerifyOtpRequest, ResendOtpRequest
# from api.core.security import hash_password, verify_password
# from api.core.otp import (
#     OTP_LENGTH, OTP_EXPIRE_MINUTES, OTP_RATE_LIMIT, OTP_RATE_WINDOW,
#     generate_otp, send_otp,
# )
# from api.users.utils import get_user_id
# from api.users.logic import get_user_by_email
# from api.core.logging import logger


# =============================================================================
# REGISTER
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
    - Sends OTP
    - Creates anti-replay registration token only after OTP succeeds
    """

    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Logged in user cannot create account",
        )

    # ------------------------------------------------------------------
    # 1. Validate country first
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

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
    await db.commit()
    await db.refresh(new_user)

    user_id = get_user_id(new_user)

    # ------------------------------------------------------------------
    # 4. Send OTP first
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
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise

        logger.error(
            "Failed to send registration OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error while sending registration OTP for user_id=%s",
            user_id,
        )
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


# =============================================================================
# VERIFY REGISTRATION OTP
# =============================================================================

async def verify_registration_otp(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: Redis,
) -> ReadUser:
    """
    Step 2: Verify registration OTP and mark the user as verified.

    - Validates anti-replay session token
    - Atomically consumes the OTP
    - Marks the user as verified
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
    # 2. Validate registration session token (do NOT delete yet)
    # ------------------------------------------------------------------
    reg_key = f"reg_attempt:{data.account_token}"
    stored_id = await redis.get(reg_key)

    if not stored_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid",
        )

    if isinstance(stored_id, bytes):
        stored_id = stored_id.decode()

    try:
        stored_user_id = int(stored_id)
    except (TypeError, ValueError):
        await redis.delete(reg_key)
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
    # 3. Atomically consume OTP
    # ------------------------------------------------------------------
    # Key format produced by generate_and_send_otp:
    #   otp:{code}:{user_id}:registration
    otp_key = f"otp:{data.otp_code}:{user_id}:registration"

    consumed = await redis.getdel(otp_key)
    if not consumed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    # ------------------------------------------------------------------
    # 4. Both session + OTP are valid → consume the session token
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

    return ReadUser.model_validate(user)


# =============================================================================
# OTP HELPERS
# =============================================================================

def generate_otp() -> str:
    """Generate a 6-digit OTP."""
    return "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))


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
    Generate OTP, store it in Redis FIRST, then queue email delivery.

    Design principle:
        The OTP must be valid the moment this function returns,
        regardless of whether the email actually arrives.
    """

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 1. Rate limit
    # ------------------------------------------------------------------
    rate_key = f"otp_rate:{user_id}:{otp_type}"
    count = await redis.incr(rate_key)

    if count == 1:
        await redis.expire(rate_key, OTP_RATE_WINDOW)

    if count > OTP_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Try again in 1 hour.",
        )

    # ------------------------------------------------------------------
    # 2. Generate + store OTP (source of truth)
    # ------------------------------------------------------------------
    otp = generate_otp()
    otp_key = f"otp:{otp}:{user_id}:{otp_type}"

    await redis.set(
        otp_key,
        "1",
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    recipient_email = override_email if override_email else user.email

    logger.info(
        "OTP stored for user_id=%s (type=%s). Queuing email...",
        user_id,
        otp_type,
    )

    # ------------------------------------------------------------------
    # 3. Queue email (best-effort)
    # ------------------------------------------------------------------
    background_tasks.add_task(
        send_otp,
        email=recipient_email,
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
    Resend OTP for an in-progress registration or login flow.

    Requires a still-valid account_token from the original request.
    """

    user = await get_user_by_email(db, data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # Validate the original session token is still active
    # ------------------------------------------------------------------
    if data.otp_type == "registration":
        token_prefix = "reg_attempt"
    elif data.otp_type == "login":
        token_prefix = "login_attempt"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OTP type for resend",
        )

    stored_id = await redis.get(f"{token_prefix}:{data.account_token}")

    if not stored_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired - please start over",
        )

    if isinstance(stored_id, bytes):
        stored_id = stored_id.decode()

    try:
        if int(stored_id) != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session expired - please start over",
            )
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired - please start over",
        )

    # ------------------------------------------------------------------
    # Resend
    # ------------------------------------------------------------------
    subject = (
        "Verify your account"
        if data.otp_type == "registration"
        else "Your login OTP"
    )

    await generate_and_send_otp(
        user=user,
        otp_type=data.otp_type,
        subject=subject,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )

    return {"message": "A new OTP has been sent to your email"}



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
        )

    # Resolve support email (country → fallback)
    support_email = settings.mail_username

    if user.country_id:
        country = await db.get(Country, user.country_id)
        if country and country.email_support:
            support_email = country.email_support

    background_tasks.add_task(
        send_support_message,
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


async def initiate_login(
    data: LoginRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Validate credentials and send login OTP.

    Notes:
    - Disabled and unverified users are intentionally allowed
      to reach complete_login so the frontend can show
      specific status messages.
    - The anti-replay login token is created only after
      the OTP has been successfully generated.
    """

    # ------------------------------------------------------------------
    # 1. Already logged in
    # ------------------------------------------------------------------
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Already logged in",
        )

    # ------------------------------------------------------------------
    # 2. Validate credentials (same error → prevents enumeration)
    # ------------------------------------------------------------------
    user = await get_user_by_email(db, data.email)

    if user is None or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 3. Rate limiting
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
    # 4. Send OTP first
    # ------------------------------------------------------------------
    try:
        await generate_and_send_otp(
            user=user,
            otp_type="login",
            subject="Your login OTP",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
        )
    except HTTPException as e:
        # Re-raise rate-limit errors from the OTP helper
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
    # 5. Create anti-replay login token ONLY after OTP succeeds
    # ------------------------------------------------------------------
    login_token = secrets.token_urlsafe(32)

    await redis.set(
        f"login_attempt:{login_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info("Login OTP sent to user_id=%s", user_id)

    return {
        "message": "OTP sent to your email",
        "email": user.email,
        "login_token": login_token,
    }
#old
async def register_user(
    data: CreateUser,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Register new user.
    - block user if already logged in
    - Validates email uniqueness
    - Validates country exists
    - Creates user (unverified)
    - Sends OTP via email
    - Returns registration token (anti-replay)
    """
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Logged in user cannot create account"
        )
    # Check email uniqueness
    existing = await get_user_by_email(db, data.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )
    
    # Validate country exists
    country = await db.get(Country, data.country_id)  # ✅ await
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Country not found"
        )
        
    # Enforce user select a country
    if data.country_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="Country is required")
    # Create user
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
    await db.commit()
    await db.refresh(new_user)
    
    # Create registration session token (anti-replay)
    reg_token = secrets.token_urlsafe(32)
    await redis.set(
        f"reg_attempt:{reg_token}",
        str(new_user.id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )
    
    # Send OTP
    await generate_and_send_otp(
        user=new_user,
        otp_type="registration",  # ✅ Fixed typo: otp_yype → otp_type
        subject="Verify your account",
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )
    logger.info(f"New registration started for {new_user.email} (user_id={new_user.id})")
    
    return {
        "message": "OTP sent to your email",
        "email": new_user.email,
        "reg_token": reg_token,
    }


async def verify_registration_otp(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: Redis,
) -> ReadUser:
    """
    Step 2: Verify registration OTP.
    
    - Validates session token (anti-replay)
    - Validates OTP
    - Marks user as verified
    """
    # Get user
    user = await get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already verified"
        )
    
    # Validate session token
    stored_id = await redis.get(f"reg_attempt:{data.account_token}")
    if not stored_id or int(stored_id) != user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid"
        )
    await redis.delete(f"reg_attempt:{data.account_token}")
    
    # Validate OTP
    otp_key = f"otp:{data.otp_code}:{user.id}:registration"
    if not await redis.exists(otp_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid"
        )
    await redis.delete(otp_key)
    
    # Mark user as verified
    user.verified = True
    user.date_verified = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return ReadUser.model_validate(user)




#otp


def generate_otp() -> str:
    """Generate a 6-digit OTP."""
    return "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))


# =============================================================================
# OTP
# =============================================================================



async def generate_and_send_otp(
    user: User,
    otp_type: str,
    subject: str,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    override_email:str | None = None,
) -> None:
    """
    Generate OTP, store in Redis FIRST, then queue email delivery.

    Design principle:
        The OTP must be valid and usable the moment this function
        returns, REGARDLESS of whether the email actually arrives.
        Email delivery is best-effort; OTP validity is guaranteed.

        If email delivery fails (even after retries), the user can
        request a resend via the rate-limited resend endpoint - they
        are never stuck waiting on an email that silently failed.

    Args:
        user: The user to send the OTP to
        otp_type: "registration" or "login"
        subject: Email subject line
        redis: Redis client
        mailer: FastMail instance
        background_tasks: FastAPI background tasks queue
        override_email if provided send otp to this email instead of user.email
        used for change flow where OTP goes to New Email

    Raises:
        HTTPException: 429 if rate limit exceeded
    """
    user_id= get_user_id(user)
    # STEP 1: Rate limit check (before generating anything)
    rate_key = f"otp_rate:{user_id}:{otp_type}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, OTP_RATE_WINDOW)
    if count > OTP_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Try again in 1 hour."
        )

    # STEP 2: Generate OTP
    otp = generate_otp()

    # STEP 3: Store in Redis FIRST - this is the source of truth.
    # OTP is valid and verifiable from this point forward,
    # independent of email success/failure.
    otp_key = f"otp:{otp}:{user.id}:{otp_type}"
    await redis.set(
        otp_key,
        "1",
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )
    #send to override email if provided
    recipient_email=override_email if override_email else user.email

    logger.info(
        f"OTP stored in Redis for user {user.id} (type: {otp_type}). "
        f"Queuing email delivery..."
    )
    

    # STEP 4: Queue email delivery (best-effort, non-blocking).
    # If this fails after all tenacity retries inside send_otp_email,
    # the OTP above is STILL valid - user can request a resend.
    background_tasks.add_task(
        send_otp,
        email=recipient_email,
        otp=otp,
        subject=subject,
        otp_type=otp_type,
        mailer=mailer,
    )
    
    
    
    
    
# ______ RESEND OTP _______


async def resend_otp(
    data: ResendOtpRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Resend OTP for an in-progress registration or login flow.
    
    Requires a valid (unexpired) account_token from the original
    register/login request - prevents resending OTPs to arbitrary
    emails without first passing credential validation.
    
    Subject to the same OTP_RATE_LIMIT as the original send.
    """
    user = await get_user_by_email(db, data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Validate the session token is still active
    token_prefix = "reg_attempt" if data.otp_type == "registration" else "login_attempt"
    stored_id = await redis.get(f"{token_prefix}:{data.account_token}")
    if not stored_id or int(stored_id) != user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired - please start over"
        )
    
    subject = (
        "Verify your account" if data.otp_type == "registration"
        else "Your login OTP"
    )
    
    # ✅ Same function - rate limited, stores in Redis before queuing email
    await generate_and_send_otp(
        user=user,
        otp_type=data.otp_type,
        subject=subject,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )
    
    return {"message": "A new OTP has been sent to your email"}






#=================================================================
# RESEND OTP FOR UNVERIFIED USERS
#================================================================


async def resend_verification(
    data: ResendOtpRequest,         # ✅ Reuse existing schema
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Resend verification OTP to unverified user.
    Wraps existing resend_otp logic with unverified-specific checks.
    """
    # Verify user exists and is actually unverified
    user = await get_user_by_email(db, data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is already verified. Please login."
        )
    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is suspended. Please contact admin."
        )

    # ✅ Delegate to existing resend_otp - no code duplication!
    return await resend_otp(
        data=ResendOtpRequest(
            email=data.email,
            account_token=data.account_token,
            otp_type="registration",        # ← Always registration for unverified
        ),
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )







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

    Flow:
        1. Verify user exists and IS disabled
        2. Rate limit (max 3 per hour)
        3. Resolve support email:
           - Try user's country.email_support first
           - Fall back to settings.mail_username
        4. Send message to support email
        5. Return confirmation

    Args:
        data: Email + message from user
        db: Database session
        redis: Redis client
        mailer: FastMail instance
        background_tasks: FastAPI background tasks

    Returns:
        dict: Confirmation message
    """
    r: Any = redis

    # Verify user exists and is disabled
    user = await get_user_by_email(db, data.email)
    if not user or not user.disabled:
        # Generic response - don't reveal account status
        return {
            "message": "If your account exists, "
                       "your message has been sent to our support team."
        }

    user_id = get_user_id(user)

    # Rate limit - max 3 per hour
    rate_key = f"contact_admin_rate:{user_id}"
    count = await r.incr(rate_key)
    if count == 1:
        await r.expire(rate_key, 3600)
    if count > 3:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again in 1 hour."
        )

    # ✅ Resolve support email
    # Priority: country.email_support → settings.mail_username
    support_email = settings.mail_username  # fallback

    if user.country_id:
        country = await db.get(Country, user.country_id)
        if country and country.email_support:
            support_email = country.email_support
            logger.info(
                f"Using country support email: {support_email} "
                f"for country_id={user.country_id}"
            )
        else:
            logger.info(
                f"No country support email found. "
                f"Using default: {support_email}"
            )
    else:
        logger.info(
            f"User has no country. Using default: {support_email}"
        )

    # ✅ Send message to support email in background
    background_tasks.add_task(
        send_support_message,
        support_email=support_email,
        user_email=data.email,
        message=data.message,
        mailer=mailer,
    )

    logger.info(
        f"Disabled user {data.email} (id={user_id}) "
        f"sent contact message to {support_email}"
    )

    return {
        "message": "Your message has been sent to our support team. "
                   "We will review your account and get back to you."
          }

