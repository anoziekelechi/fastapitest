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




# Add to api/users/schemas.py

class RequestResetPassword(BaseModel):
    """Schema for requesting a password reset OTP."""
    model_config = ConfigDict(extra="forbid")
    
    email: EmailStr
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()


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
        return v.lower().strip()
    
    @field_validator("new_password", mode="before")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return validate_password(v)



# Add to api/users/logics.py

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
    existing_tokens = await redis.smembers(f"user_refresh:{user_id}")
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




# Add to api/users/routes.py

@router.post(
    "/password/reset-request",
    status_code=status.HTTP_200_OK,
    summary="Request password reset OTP",
)
async def request_password_reset(
    data: RequestResetPassword,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    redis: RedisDep = Depends(),
    mailer: MailDep = Depends(),
    # ✅ Optional - not logged in is expected here
    current_user: ReadUser | None = Depends(get_optional_user),
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
    status_code=status.HTTP_200_OK,
    summary="Complete password reset with OTP",
)
async def complete_password_reset(
    data: ResetPassword,
    db: AsyncSession = Depends(get_db_session),
    redis: RedisDep = Depends(),
    # ✅ Optional - not logged in is expected here
    current_user: ReadUser | None = Depends(get_optional_user),
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



# Add to api/users/schemas.py

class RequestEmailChange(BaseModel):
    """Schema for requesting an email change."""
    model_config = ConfigDict(extra="forbid")
    
    current_password: str = Field(..., min_length=8)
    new_email: EmailStr
    
    @field_validator("new_email", mode="before")
    @classmethod
    def normalize_new_email(cls, v: str) -> str:
        return v.lower().strip()


class VerifyEmailChange(BaseModel):
    """Schema for verifying the new email OTP."""
    model_config = ConfigDict(extra="forbid")
    
    otp_code: str = Field(..., pattern=r"^\d{6}$")
    email_change_token: str = Field(..., min_length=32)  # Anti-replay token



# Add to api/users/logics.py

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
    
    # Create a temporary User-like object with new_email
    # so generate_and_send_otp sends to the NEW email address
    # We can't pass `user` directly since user.email is still the OLD email
    class TempUser:
        """Temporary object to direct OTP to new email address."""
        id = user_id
        email = data.new_email
    
    await generate_and_send_otp(
        user=TempUser(),                 # ← OTP goes to NEW email
        otp_type="email_change",
        subject="Verify your new email address",
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
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


# Add to api/users/routes.py

@router.post(
    "/email/change-request",
    status_code=status.HTTP_200_OK,
    summary="Request email change - sends OTP to new email",
    dependencies=[Depends(require_csrf)],   # ✅ CSRF on sensitive mutations
)
async def request_email_change_route(
    data: RequestEmailChange,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    redis: RedisDep = Depends(),
    mailer: MailDep = Depends(),
    current_user: ReadUser = Depends(get_authenticated_user),  # ✅ Required (not optional)
) -> dict:
    """
    Request an email address change.

    - Requires authentication
    - Requires CSRF token
    - Verifies current password before proceeding
    - Sends OTP to the NEW email address (proves ownership)
    - Returns email_change_token needed for verify step
    """
    return await request_email_change(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/email/verify",
    response_model=ReadUser,
    status_code=status.HTTP_200_OK,
    summary="Verify new email OTP and complete email change",
    dependencies=[Depends(require_csrf)],   # ✅ CSRF on mutations
)
async def verify_new_email_route(
    data: VerifyEmailChange,
    db: AsyncSession = Depends(get_db_session),
    redis: RedisDep = Depends(),
    current_user: ReadUser = Depends(get_authenticated_user),  # ✅ Required
) -> ReadUser:
    """
    Verify OTP sent to new email and complete the email change.

    - Requires authentication (same session from step 1)
    - Requires CSRF token
    - Validates email_change_token (anti-replay)
    - Validates OTP
    - Updates email in database
    - Returns updated user profile
    """
    return await verify_new_email(
        data=data,
        db=db,
        redis=redis,
        current_user=current_user,
)
