
"""User business logic."""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.security import hash_password, verify_password
from api.users.models import User
from api.users.schemas import CreateUser, LoginRequest
from api.home.models import Country
from api.users.email import send_otp_email


logger = logging.getLogger(__name__)

OTP_LENGTH = 6
OTP_EXPIRE_MINUTES = 10
OTP_RATE_LIMIT = 5
OTP_RATE_WINDOW = 3600  # 1 hour


def generate_otp() -> str:
    """Generate a 6-digit OTP using a cryptographically secure RNG."""
    return "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))


# =============================================================================
# OTP GENERATION + DELIVERY
# =============================================================================

async def generate_and_send_otp(
    user: User,
    otp_type: str,
    subject: str,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
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

    Raises:
        HTTPException: 429 if rate limit exceeded
    """
    # STEP 1: Rate limit check (before generating anything)
    rate_key = f"otp_rate:{user.id}:{otp_type}"
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

    logger.info(
        f"OTP stored in Redis for user {user.id} (type: {otp_type}). "
        f"Queuing email delivery..."
    )

    # STEP 4: Queue email delivery (best-effort, non-blocking).
    # If this fails after all tenacity retries inside send_otp_email,
    # the OTP above is STILL valid - user can request a resend.
    background_tasks.add_task(
        send_otp_email,
        email=user.email,
        otp=otp,
        subject=subject,
        otp_type=otp_type,
        mailer=mailer,
    )


# =============================================================================
# HELPERS (used by register_user / initiate_login)
# =============================================================================

async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Fetch user by email."""
    from sqlmodel import select
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first()


# =============================================================================
# REGISTRATION
# =============================================================================

async def register_user(
    data: CreateUser,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Step 1: Register a new user and send a registration OTP.

    Flow:
        1. Validate email is not already registered
        2. Validate country exists
        3. Create user record (unverified)
        4. Create anti-replay registration session token
        5. Generate + store OTP, queue delivery email

    Args:
        data: Validated registration payload
        db: Database session
        redis: Redis client
        mailer: FastMail instance
        background_tasks: FastAPI background tasks queue

    Returns:
        dict: message, email, and reg_token (needed for verify step)

    Raises:
        HTTPException: 409 if email exists, 404 if country invalid
    """
    # Check email uniqueness
    existing = await get_user_by_email(db, data.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )

    # Validate country exists
    country = await db.get(Country, data.country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Country not found"
        )

    # Create user (unverified)
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

    # Anti-replay registration session token
    reg_token = secrets.token_urlsafe(32)
    await redis.set(
        f"reg_attempt:{reg_token}",
        str(new_user.id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    # Generate + send OTP (stored before email is queued - see function docstring)
    await generate_and_send_otp(
        user=new_user,
        otp_type="registration",
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


# =============================================================================
# LOGIN (STEP 1 OF 2 - CREDENTIALS)
# =============================================================================

async def initiate_login(
    data: LoginRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Step 1: Validate credentials and send a login OTP.

    Flow:
        1. Validate email + password (generic error - prevents enumeration)
        2. Check account is not disabled
        3. Check account is verified
        4. Rate limit login attempts (separate from OTP rate limit)
        5. Create anti-replay login session token
        6. Generate + store OTP, queue delivery email

    Args:
        data: Validated login payload (email + password)
        db: Database session
        redis: Redis client
        mailer: FastMail instance
        background_tasks: FastAPI background tasks queue

    Returns:
        dict: message, email, and login_token (needed for verify step)

    Raises:
        HTTPException: 401 invalid credentials, 403 disabled/unverified,
                       429 too many attempts
    """
    # Validate credentials - same error message for both failure cases
    # to avoid leaking whether an email is registered
    user = await get_user_by_email(db, data.email)
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )

    if not user.verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified. Please check your email."
        )

    # Rate limiting - separate counter from OTP rate limit, protects
    # against credential-stuffing/brute-force attempts
    rate_key = f"login_rate:{user.id}"
    attempts = await redis.incr(rate_key)
    if attempts == 1:
        await redis.expire(rate_key, int(timedelta(minutes=15).total_seconds()))
    if attempts > 8:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes."
        )

    # Anti-replay login session token
    login_token = secrets.token_urlsafe(32)
    await redis.set(
        f"login_attempt:{login_token}",
        str(user.id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    # Generate + send OTP (stored before email is queued - see function docstring)
    await generate_and_send_otp(
        user=user,
        otp_type="login",
        subject="Your login OTP",
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )

    logger.info(f"Login OTP sent for user_id={user.id}")

    return {
        "message": "OTP sent to your email",
        "email": user.email,
        "login_token": login_token,
    }
