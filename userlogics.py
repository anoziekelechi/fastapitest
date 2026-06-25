#route
@router.put(
    "/profile/names",
    response_model=ReadUser,
    status_code=status.HTTP_200_OK,
    summary="Update user names",
    dependencies=[Depends(require_csrf)],   # ✅ CSRF on mutations
)
async def update_names(
    data: UpdateNames,
    db: AsyncSession = Depends(get_db_session),
    current_user: ReadUser = Depends(get_authenticated_user),
) -> ReadUser:
    """
    Update surname and/or othernames.
    
    - Requires authentication (access_token cookie)
    - Requires CSRF token in X-CSRF-Token header
    - At least one field must be provided
    - Skips fields that match current values
    """
    return await update_user_names(
        data=data,
        db=db,
        current_user=current_user,
    )

#logics
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




"""
User business logic.

Flow:
    Registration: POST /register → OTP email → POST /verify-registration
    Login:        POST /login    → OTP email → POST /verify-login
    Logout:       POST /logout   (clears cookies + revokes tokens)
"""
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import BackgroundTasks, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.core.redis import RedisDep
from api.core.mail import MailDep
from api.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    revoke_refresh_token,
    set_auth_cookies,
    clear_auth_cookies,
    generate_csrf_token,
    verify_csrf_token,
    REFRESH_TOKEN_COOKIE,
)
from api.core.settings import get_settings
from api.users.models import User
from api.users.schemas import (
    CreateUser,
    LoginRequest,
    ReadUser,
    VerifyOtpRequest,
    UpdateNames,
    UpdatePassword,
    VerifyPassword,
)
from api.home.models import Country
from api.users.email import send_otp_email


settings = get_settings()

# OTP Configuration
OTP_LENGTH = 6
OTP_EXPIRE_MINUTES = 10
OTP_RATE_LIMIT = 5
OTP_RATE_WINDOW = 3600  # 1 hour


# =============================================================================
# HELPERS
# =============================================================================

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
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,
) -> None:
    """
    Generate OTP, store in Redis, and send via email.
    
    Rate limited to 5 requests per hour per user per type.
    OTP expires after 10 minutes.
    Key format: otp:{otp}:{user_id}:{otp_type}
    """
    # Rate limiting
    rate_key = f"otp_rate:{user.id}:{otp_type}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, OTP_RATE_WINDOW)
    if count > OTP_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many OTP requests. Try again in 1 hour."
        )
    
    # Generate OTP
    otp = generate_otp()
    
    # Store in Redis - key includes OTP for O(1) lookup
    otp_key = f"otp:{otp}:{user.id}:{otp_type}"
    await redis.set(
        otp_key,
        "1",
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )
    
    # Send email in background
    background_tasks.add_task(
        send_otp_email,
        email=user.email,
        otp=otp,
        subject=subject,
        otp_type=otp_type,
        mailer=mailer,
    )


# =============================================================================
# REGISTRATION
# =============================================================================

async def register_user(
    data: CreateUser,
    db: AsyncSession,
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Step 1: Register new user.
    
    - Validates email uniqueness
    - Validates country exists
    - Creates user (unverified)
    - Sends OTP via email
    - Returns registration token (anti-replay)
    """
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
    
    return {
        "message": "OTP sent to your email",
        "email": new_user.email,
        "reg_token": reg_token,
    }


async def verify_registration_otp(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: RedisDep,
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
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Step 1: Validate credentials, send OTP.
    
    - Validates email + password
    - Rate limits login attempts
    - Sends OTP via email
    - Returns login token (anti-replay)
    """
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
    
    return {
        "message": "OTP sent to your email",
        "email": user.email,
        "login_token": login_token,
    }


async def complete_login(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: RedisDep,
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
    
    # Create tokens
    access_token = create_access_token(user.id)
    refresh_token = await create_refresh_token(user.id, redis)
    csrf_token = await generate_csrf_token(user.id, redis)
    
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
    redis: RedisDep,
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
    redis: RedisDep,
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
