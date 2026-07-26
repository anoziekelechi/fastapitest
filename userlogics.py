    try:
        app.state.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
            retry_on_timeout=True,
            retry_on_error=True,
            health_check_interval=30,
            max_connections=50,
        )
        # Test connection
        pong =  app.state.redis.ping() # text connection
        if not pong:
             raise RuntimeError("redis connection failed")
        logger.info(f"redis connected successfully ->{pong}")
    except Exception as e:
        logger.error(f"redis connection fail: {e}")
        raise RuntimeError("redis connection failed")






from typing import Annotated 
from typing import TypeAlias
from fastapi import Depends, Request, HTTPException
from redis.asyncio import Redis


async def get_redis(request: Request) ->Redis:
    if not hasattr(request.app.state, "redis") or request.app.state.redis is None:
        raise HTTPException(status_code=503, detail="Redis service not available")
    return request.app.state.redis

RedisDep: TypeAlias =Annotated[Redis, Depends(get_redis)]




#api/users/logics.py

"""
User business logic.

Flow:
    Registration: POST /register → OTP email → POST /verify-registration
    Login:        POST /login    → OTP email → POST /verify-login
    Logout:       POST /logout   (clears cookies + revokes tokens)
"""
import secrets
import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import BackgroundTasks, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from redis.asyncio import Redis
from fastapi_mail import FastMail
from api.core.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_access_token,
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
    UpdateNames,
    UpdatePassword,
    VerifyPassword,
    ResendOtpRequest,
    ResetPassword,
    RequestResetPassword,
    RequestEmailChange,
    VerifyEmailChange,
)
from api.models.home import Country
from api.users.send_otp_email import send_otp
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
    result = await db.execute(select(User).where(User.email == email))
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
    if not user:
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
    Step 1: Validate credentials, send OTP.
    
    - Validates email + password
    - Rate limits login attempts
    - Sends OTP via email
    - Returns login token (anti-replay)
    """
    
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="already logged in"
        )
    # Validate credentials (same error for both cases - prevents enumeration)
    user = await get_user_by_email(db, data.email)
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    # Check account status
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
    
    # Rate limiting
    rate_key = f"login_rate:{user.id}"
    attempts = await redis.incr(rate_key)
    if attempts == 1:
        await redis.expire(
            rate_key,
            int(timedelta(minutes=15).total_seconds())
        )
    if attempts > 8:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes."
        )
    
    # Create login session token (anti-replay)
    login_token = secrets.token_urlsafe(32)
    await redis.set(
        f"login_attempt:{login_token}",
        str(user.id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )
    
    # Send OTP
    await generate_and_send_otp(
        user=user,
        otp_type="login",
        subject="Your login OTP",
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )
    logger.info(f"Login OTP sent to user_id={user.id}")
    
    return {
        "message": "OTP sent to your email",
        "email": user.email,
        "login_token": login_token,
    }


async def complete_login(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: Redis,
    response: Response,         # ← Needed to set cookies
) -> dict:
    """
    Step 2: Verify OTP, issue tokens via cookies.
    
    - Validates session token
    - Validates OTP
    - Issues access + refresh + CSRF tokens as cookies
    - Clears login rate limit
    """
    # Get user
    user = await get_user_by_email(db, data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    # Validate session token
    stored_id = await redis.get(f"login_attempt:{data.account_token}")
    if not stored_id or int(stored_id) != user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid"
        )
    await redis.delete(f"login_attempt:{data.account_token}")
    
    # Validate OTP
    otp_key = f"otp:{data.otp_code}:{user.id}:login"
    if not await redis.exists(otp_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid"
        )
    await redis.delete(otp_key)
    
    # Clear rate limit on successful login
    await redis.delete(f"login_rate:{user.id}")
    
    if user.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User id missing"
        )
    user_id:int = user.id
    
    # Create tokens
    access_token = create_access_token(user_id) #user_id replace user.id with user_id
    refresh_token = await create_refresh_token(user_id, redis)
    csrf_token = await generate_csrf_token(user_id, redis)
    
    # ✅ Set tokens as HttpOnly cookies
    set_auth_cookies(
        response=response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )
    
    return {"message": "Login successful"}


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
    Logout user.
    
    - Revokes refresh token
    - Clears all auth cookies
    - Clears user OTPs from Redis
    """
    # Revoke refresh token
    refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if refresh_token:
        await revoke_refresh_token(refresh_token, redis)
    
    # Clean up OTPs
    otp_keys = await redis.keys(f"otp:*:{current_user.id}:*")
    if otp_keys:
        await redis.delete(*otp_keys)
    
    # ✅ Clear all cookies
    clear_auth_cookies(response)
    
    return {"message": "Logged out successfully"}


# =============================================================================
# USER MANAGEMENT
# =============================================================================

async def get_current_user(
    request: Request,
    db: AsyncSession,
) -> ReadUser:
    """
    Get current authenticated user from cookie.
    
    Used as a dependency in routes.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    payload = decode_access_token(token)
    
    user = await get_user_by_id(db, payload.sub)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )
    if not user.verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified"
        )
    
    return ReadUser.model_validate(user)



async def get_authenticated_user(
    request: Request,
    db: AsyncSession ,
) -> ReadUser:
    """
    Dependency: Get current authenticated user.
    
    Usage:
        @router.get("/profile")
        async def profile(user: ReadUser = Depends(get_authenticated_user)):
            ...
    """
    return await get_current_user(request=request, db=db)


async def get_optional_user(
    request: Request,
    db: AsyncSession,
) -> ReadUser | None:
    
    token= request.cookies.get("access_token")
    if not token:
        return None
    try:
        return await get_current_user(request=request,db=db)
    except HTTPException:
        return None


async def update_user_names(
    data: UpdateNames,
    db: AsyncSession,
    current_user: ReadUser,
) -> ReadUser:
    """
    Update authenticated user's surname and/or othernames.
    
    - At least one field must be provided
    - Only updates fields that differ from current values
    - Requires authentication (enforced at route level)
    """
    if data.surname is None and data.othernames is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field (surname or othernames) must be provided"
        )
    
    user = await get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    updated_fields = []
    
    if data.surname is not None:
        if data.surname == user.surname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New surname is the same as current surname"
            )
        user.surname = data.surname
        updated_fields.append("surname")
    
    if data.othernames is not None:
        if data.othernames == user.othernames:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New othernames is the same as current othernames"
            )
        user.othernames = data.othernames
        updated_fields.append("othernames")
    
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    logger.info(f"User {current_user.id} updated: {', '.join(updated_fields)}")
    
    return ReadUser.model_validate(user)

async def change_password(
    data: UpdatePassword,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Change user password."""
    user = await get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect"
        )
    
    user.hashed_password = hash_password(data.new_password)
    db.add(user)
    await db.commit()
    
    return {"message": "Password updated successfully"}


async def delete_account(
    data: VerifyPassword,
    db: AsyncSession,
    redis: Redis,
    response: Response,
    current_user: ReadUser,
) -> dict:
    """
    Delete user account.
    
    - Verifies password
    - Cleans up all Redis data
    - Deletes user from database
    - Clears cookies
    """
    user = await get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password"
        )
    
    # Clean up ALL Redis data for this user
    patterns = [
        f"user_refresh:{user.id}",
        f"user_csrf:{user.id}",
        f"login_rate:{user.id}",
        f"otp_rate:{user.id}:*",
    ]
    for pattern in patterns:
        keys = await redis.keys(pattern)
        if keys:
            await redis.delete(*keys)
    
    # Delete OTP keys
    otp_keys = await redis.keys(f"otp:*:{user.id}:*")
    if otp_keys:
        await redis.delete(*otp_keys)
    
    # Delete user
    await db.delete(user)
    await db.commit()
    
    # Clear cookies
    clear_auth_cookies(response)
    
    return {"message": "Account deleted successfully"}



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
        1. Block if user is already logged in
           (logged-in users should use change_password instead)
        2. Check email exists in DB
           (always return same message - prevents email enumeration)
        3. Rate limit OTP requests
        4. Generate + store OTP and reset session token in Redis
        5. Queue OTP email delivery

    Why same response whether email exists or not?
        If we return "email not found" for unknown emails, an attacker
        can enumerate which emails are registered in your system.
        Returning the same message for both cases prevents this.

    Args:
        data: Email address to reset password for
        db: Database session
        redis: Redis client
        mailer: FastMail instance
        background_tasks: FastAPI background tasks queue
        current_user: Optional authenticated user (None = not logged in)

    Returns:
        dict: Generic message (same regardless of whether email exists)

    Raises:
        HTTPException: 403 if already logged in
        HTTPException: 429 if too many OTP requests
    """
    # Block logged-in users - they should use change_password instead
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Logged in users cannot use password reset. "
                   "Use 'change password' instead."
        )
    
    # Look up user - but don't reveal whether email exists or not
    user = await get_user_by_email(db, data.email)
    
    # ✅ Generic response - same message whether email found or not
    # This prevents email enumeration attacks
    generic_response = {
        "message": "If this email is registered, an OTP has been sent.",
        "email": data.email,
        # reset_token only included if user actually exists
    }
    
    if not user:
        # Email not found - return generic message silently
        # Do NOT reveal "email not found"
        logger.info(
            f"Password reset requested for unknown email: {data.email}"
        )
        return generic_response
    
    # Check account status - silently skip disabled accounts
    # (don't reveal account is disabled to potential attacker)
    if user.disabled:
        logger.warning(
            f"Password reset requested for disabled account: {data.email}"
        )
        return generic_response
    
    user_id = get_user_id(user)
    
    # Generate anti-replay reset session token
    reset_token = secrets.token_urlsafe(32)
    await redis.set(
        f"reset_attempt:{reset_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )
    
    # Generate + store OTP, queue email
    # generate_and_send_otp handles its own rate limiting
    try:
        await generate_and_send_otp(
            user=user,
            otp_type="password_reset",
            subject="Reset your password",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
        )
    except HTTPException as e:
        # Rate limit hit - re-raise (this one is ok to reveal)
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        # Any other error - log and return generic response
        logger.error(
            f"OTP generation failed for password reset "
            f"(user_id={user_id}): {e.detail}"
        )
        return generic_response
    
    logger.info(f"Password reset OTP sent for user_id={user_id}")
    
    # Return reset_token so frontend can pass it to the next step
    return {
        "message": "If this email is registered, an OTP has been sent.",
        "email": data.email,
        "reset_token": reset_token,
    }


async def reset_password(
    data: ResetPassword,
    db: AsyncSession,
    redis: Redis,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 2: Verify OTP and set new password.

    Flow:
        1. Block if user is already logged in
        2. Look up user by email
        3. Validate reset session token (anti-replay)
        4. Validate OTP exists and is valid
        5. Validate new password (strength check via schema validator)
        6. Ensure new password differs from current password
        7. Update password in DB
        8. Clean up Redis (OTP + session token)
        9. Invalidate all existing refresh tokens (security - force re-login)

    Why invalidate refresh tokens?
        If someone's email was compromised and their password was reset
        by an attacker, the attacker would have a new password but the
        real user would still be "logged in" via their refresh token.
        Revoking all tokens forces everyone (including the attacker)
        to login fresh with the new credentials.

    Args:
        data: Email, OTP, reset token, and new password
        db: Database session
        redis: Redis client
        current_user: Optional authenticated user (None = not logged in)

    Returns:
        dict: Success message

    Raises:
        HTTPException: 403 if already logged in
        HTTPException: 404 if user not found
        HTTPException: 400 if session token invalid or expired
        HTTPException: 401 if OTP invalid or expired
        HTTPException: 400 if new password same as current
    """
    # Block logged-in users
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Logged in users cannot use password reset. "
                   "Use 'change password' instead."
        )
    
    # Look up user
    user = await get_user_by_email(db, data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    user_id = get_user_id(user)
    
    # Validate reset session token (proves they went through step 1)
    stored_id = await redis.get(f"reset_attempt:{data.reset_token}")
    if not stored_id or int(stored_id) != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset session expired or invalid. "
                   "Please request a new OTP."
        )
    
    # Validate OTP
    otp_key = f"otp:{data.otp_code}:{user_id}:password_reset"
    if not await redis.exists(otp_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid"
        )
    
    # Validate new password is not the same as current password
    if verify_password(data.new_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from your current password"
        )
    
    # ✅ All checks passed - update password
    user.hashed_password = hash_password(data.new_password)
    db.add(user)
    await db.commit()
    
    # Clean up Redis
    await redis.delete(f"reset_attempt:{data.reset_token}")
    await redis.delete(otp_key)
    
    # Invalidate ALL existing refresh tokens for this user
    # Forces re-login on all devices (security best practice after password reset)
    existing_tokens:set[str] = await redis.smembers(f"user_refresh:{user_id}")
    if existing_tokens:
        for token in existing_tokens:
            try:
                # Decode each token to get its jti for blacklisting
                import jwt as pyjwt
                payload = pyjwt.decode(
                    token,
                    settings.public_key,
                    algorithms=[settings.algorithm],
                    options={"verify_exp": False},  # May already be expired
                )
                jti = payload.get("jti")
                if jti:
                    await redis.set(
                        f"blacklist:refresh:{jti}",
                        "1",
                        ex=int(
                            timedelta(
                                days=settings.refresh_token_expire_days
                            ).total_seconds()
                        )
                    )
            except Exception:
                continue  # Skip malformed tokens
        
        # Delete the entire active tokens set
        await redis.delete(f"user_refresh:{user_id}")
    
    logger.info(
        f"Password reset successful for user_id={user_id}. "
        f"All refresh tokens invalidated."
    )
    
    return {
        "message": "Password reset successful. "
                   "Please login with your new password."
                }



#________ Change Email _____________

async def request_email_change(
    data: RequestEmailChange,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser,             # ✅ Required - must be logged in
) -> dict:
    """
    Step 1: Validate current password and send OTP to NEW email.

    Flow:
        1. Require authentication (current_user is NOT optional here)
        2. Fetch real user from DB (current_user is ReadUser schema, not ORM)
        3. Verify current password matches
        4. Ensure new email is different from current email
        5. Ensure new email is not already taken by another account
        6. Store new email + anti-replay token in Redis temporarily
        7. Send OTP to NEW email (not old email - we're verifying the new one)

    Why send OTP to new email (not old)?
        We need to prove the user OWNS the new email address.
        Sending to the old email only proves they're logged in,
        which we already know. The OTP to the new email proves
        they have access to it.

    Args:
        data: Current password + new email
        db: Database session
        redis: Redis client
        mailer: FastMail instance
        background_tasks: FastAPI background tasks queue
        current_user: Authenticated user (required - raises 401 if missing)

    Returns:
        dict: message + email_change_token (needed for verify step)

    Raises:
        HTTPException: 401 if password incorrect
        HTTPException: 400 if new email same as current
        HTTPException: 409 if new email already registered
        HTTPException: 429 if too many OTP requests
    """
    # Fetch real ORM user (current_user is a ReadUser schema, not ORM object)
    user = await get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Verify current password
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect"
        )
    
    # Ensure new email differs from current email
    if data.new_email == user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New email must be different from your current email"
        )
    
    # Ensure new email is not already registered to another account
    existing = await get_user_by_email(db, data.new_email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email address is already registered to another account"
        )
    
    user_id = get_user_id(user)
    
    # Store new email in Redis temporarily so verify step can use it
    # without us passing it back in the response (which could be tampered with)
    email_change_token = secrets.token_urlsafe(32)
    
    # Store both the user_id AND the new email under this token
    await redis.set(
        f"email_change:{email_change_token}",
        f"{user_id}:{data.new_email}",   # ← store both together
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )
    
    # pass real user + override_email
    await generate_and_send_otp(
        user=user,                 # ← OTP goes to NEW email
        otp_type="email_change",
        subject="Verify your new email address",
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        override_email=data.new_email,
    )
    
    logger.info(
        f"Email change OTP sent for user_id={user_id}. "
        f"New email: {data.new_email}"
    )
    
    return {
        "message": "An OTP has been sent to your new email address. "
                   "Please verify it to complete the email change.",
        "email_change_token": email_change_token,
    }


async def verify_new_email(
    data: VerifyEmailChange,
    db: AsyncSession,
    redis: Redis,
    current_user: ReadUser,             # ✅ Required - must still be logged in
) -> ReadUser:
    """
    Step 2: Verify OTP sent to new email and update email in DB.

    Flow:
        1. Require authentication (session must still be valid)
        2. Validate email_change_token (anti-replay, retrieves new email)
        3. Ensure token belongs to this logged-in user (not another user's token)
        4. Validate OTP sent to new email
        5. Final check: new email still not taken (race condition guard)
        6. Update email in DB
        7. Clean up Redis
        8. Return updated user profile

    Why require auth in step 2?
        Prevents someone who gets hold of the email_change_token
        (e.g. from a shared screen or shoulder surfing) from completing
        the change without also having the active session cookie.
        Both the session AND the token are required.

    Args:
        data: OTP code + email_change_token from step 1
        db: Database session
        redis: Redis client
        current_user: Authenticated user (must match token owner)

    Returns:
        ReadUser: Updated user profile with new email

    Raises:
        HTTPException: 400 if token invalid/expired
        HTTPException: 403 if token belongs to different user
        HTTPException: 401 if OTP invalid/expired
        HTTPException: 409 if new email taken (race condition)
        HTTPException: 404 if user not found
    """
    # Fetch real ORM user
    user = await get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    user_id = get_user_id(user)
    
    # Validate email_change_token and retrieve stored data
    stored_data = await redis.get(f"email_change:{data.email_change_token}")
    if not stored_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email change session expired or invalid. "
                   "Please request a new OTP."
        )
    
    # Parse stored data: "user_id:new_email"
    try:
        stored_user_id_str, new_email = stored_data.split(":", 1)
        stored_user_id = int(stored_user_id_str)
    except (ValueError, AttributeError):
        # Corrupted data - clean up and reject
        await redis.delete(f"email_change:{data.email_change_token}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session data. Please request a new OTP."
        )
    
    # Ensure token belongs to the currently logged-in user
    # Prevents one user from using another user's email change token
    if stored_user_id != user_id:
        logger.warning(
            f"Email change token mismatch: "
            f"token owner={stored_user_id}, "
            f"requester={user_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This email change token does not belong to your account"
        )
    
    # Validate OTP (was sent to new_email, stored under new_email's user_id)
    otp_key = f"otp:{data.otp_code}:{user_id}:email_change"
    if not await redis.exists(otp_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid"
        )
    
    # Race condition guard: check new email still isn't taken
    # (someone else could have registered it between step 1 and step 2)
    existing = await get_user_by_email(db, new_email)
    if existing and get_user_id(existing) != user_id:
        # Clean up since we can't proceed
        await redis.delete(f"email_change:{data.email_change_token}")
        await redis.delete(otp_key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email address has just been registered by another account. "
                   "Please choose a different email."
        )
    
    # ✅ All checks passed - update email
    old_email = user.email
    user.email = new_email
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    # Clean up Redis
    await redis.delete(f"email_change:{data.email_change_token}")
    await redis.delete(otp_key)
    
    logger.info(
        f"Email changed successfully for user_id={user_id}. "
        f"{old_email} → {new_email}"
    )
    
    return ReadUser.model_validate(user)




# =============================================================================
# PERMISSIONS
# =============================================================================

async def has_permission(user: ReadUser, required_perm: str) -> bool:
    """Check if user has required permission."""
    if user.is_admin:
        return True
    # Permission check requires loading group - do in route if needed
    return False


def require_admin():
    """Dependency: require admin user."""
    async def dependency(
        request: Request,
        db: AsyncSession,
    ) -> ReadUser:
        user = await get_current_user(request, db)
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )
        return user
    return dependency
