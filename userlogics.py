#updated reset password 
import secrets
from datetime import timedelta

from fastapi import BackgroundTasks, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_mail import FastMail


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
        1. Block if user is already logged in.
        2. Look up the email without revealing whether it exists.
        3. Silently ignore unknown/disabled accounts.
        4. Generate and send the OTP.
        5. Only after successful OTP generation, create the reset session token.
        6. Return the reset session token.

    Security:
        - Prevents logged-in users from using password reset.
        - Uses a generic response to prevent email enumeration.
        - Does not create a reset session when OTP generation fails.
        - Reset session token is cryptographically random.
    """

    # -------------------------------------------------------------------------
    # 1. Block logged-in users
    # -------------------------------------------------------------------------
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Logged in users cannot use password reset. "
                "Use 'change password' instead."
            ),
        )

    # -------------------------------------------------------------------------
    # 2. Generic response
    # -------------------------------------------------------------------------
    generic_response = {
        "message": "If this email is registered, an OTP has been sent.",
    }

    # -------------------------------------------------------------------------
    # 3. Look up user
    # -------------------------------------------------------------------------
    user = await get_user_by_email(db, data.email)

    if not user:
        logger.info("Password reset requested for unknown email")
        return generic_response

    # -------------------------------------------------------------------------
    # 4. Silently ignore disabled accounts
    # -------------------------------------------------------------------------
    if user.disabled:
        logger.warning("Password reset requested for disabled account")
        return generic_response

    user_id = get_user_id(user)

    # -------------------------------------------------------------------------
    # 5. Generate + send OTP
    # -------------------------------------------------------------------------
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
        # Rate limiting is safe to expose
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise

        logger.error(
            f"OTP generation failed for password reset "
            f"(user_id={user_id}): {e.detail}"
        )
        return generic_response

    except Exception:
        logger.exception(
            f"Unexpected OTP generation failure for password reset "
            f"(user_id={user_id})"
        )
        return generic_response

    # -------------------------------------------------------------------------
    # 6. Create reset session token ONLY after OTP generation succeeds
    # -------------------------------------------------------------------------
    reset_token = secrets.token_urlsafe(32)

    reset_ttl = int(
        timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
    )

    await redis.set(
        f"reset_attempt:{reset_token}",
        str(user_id),
        ex=reset_ttl,
    )

    logger.info(f"Password reset OTP sent for user_id={user_id}")

    # -------------------------------------------------------------------------
    # 7. Return reset token
    # -------------------------------------------------------------------------
    return {
        "message": "If this email is registered, an OTP has been sent.",
        "reset_token": reset_token,
    }


async def reset_password(
    data: ResetPassword,
    db: AsyncSession,
    redis: Redis,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 2: Verify OTP and set a new password.

    Flow:
        1. Block if user is already logged in.
        2. Look up user by email.
        3. Validate reset session token.
        4. Retrieve OTP from Redis.
        5. Verify submitted OTP.
        6. Atomically consume OTP.
        7. Ensure new password differs from current password.
        8. Update password.
        9. Commit database transaction.
        10. Delete reset session.
        11. Invalidate all refresh tokens.
        12. Return success.

    Security:
        - Reset requires both email + reset session token + OTP.
        - OTP is consumed atomically.
        - Reset session expires automatically.
        - Existing refresh sessions are revoked via revoke_all_user_tokens.
        - User must authenticate again after password reset.
    """

    # -------------------------------------------------------------------------
    # 1. Block logged-in users
    # -------------------------------------------------------------------------
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Logged in users cannot use password reset. "
                "Use 'change password' instead."
            ),
        )

    # -------------------------------------------------------------------------
    # 2. Look up user
    # -------------------------------------------------------------------------
    user = await get_user_by_email(db, data.email)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    # -------------------------------------------------------------------------
    # 3. Validate reset session token
    # -------------------------------------------------------------------------
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
        stored_id = stored_id.decode()

    try:
        stored_user_id = int(stored_id)
    except (TypeError, ValueError):
        await redis.delete(reset_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Reset session expired or invalid. "
                "Please request a new OTP."
            ),
        )

    if stored_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Reset session expired or invalid. "
                "Please request a new OTP."
            ),
        )

    # -------------------------------------------------------------------------
    # 4. Get stored OTP
    # -------------------------------------------------------------------------
    # Expected Redis key: otp:password_reset:{user_id}
    otp_key = f"otp:password_reset:{user_id}"

    stored_otp = await redis.get(otp_key)

    if not stored_otp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    if isinstance(stored_otp, bytes):
        stored_otp = stored_otp.decode()

    # -------------------------------------------------------------------------
    # 5. Verify submitted OTP
    # -------------------------------------------------------------------------
    if not secrets.compare_digest(str(data.otp_code), str(stored_otp)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    # -------------------------------------------------------------------------
    # 6. Atomically consume OTP
    # -------------------------------------------------------------------------
    consumed_otp = await redis.getdel(otp_key)

    if not consumed_otp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    if isinstance(consumed_otp, bytes):
        consumed_otp = consumed_otp.decode()

    if not secrets.compare_digest(str(data.otp_code), str(consumed_otp)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    # -------------------------------------------------------------------------
    # 7. Ensure new password differs from current password
    # -------------------------------------------------------------------------
    if verify_password(data.new_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from your current password",
        )

    # -------------------------------------------------------------------------
    # 8. Update password
    # -------------------------------------------------------------------------
    user.hashed_password = hash_password(data.new_password)
    db.add(user)

    # -------------------------------------------------------------------------
    # 9. Commit password change
    # -------------------------------------------------------------------------
    try:
        await db.commit()
    except Exception:
        await db.rollback()

        # OTP has already been consumed – this is intentional.
        logger.exception(
            f"Password reset database commit failed (user_id={user_id})"
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to reset password. Please try again.",
        )

    # -------------------------------------------------------------------------
    # 10. Delete reset session
    # -------------------------------------------------------------------------
    await redis.delete(reset_key)

    # -------------------------------------------------------------------------
    # 11. Invalidate ALL existing refresh tokens
    # -------------------------------------------------------------------------
    # Uses the shared helper that works with the JTI-based design.
    await revoke_all_user_tokens(user_id, redis)

    # -------------------------------------------------------------------------
    # 12. Success
    # -------------------------------------------------------------------------
    logger.info(
        f"Password reset successful for user_id={user_id}. "
        "All refresh tokens invalidated."
    )

    return {
        "message": (
            "Password reset successful. "
            "Please login with your new password."
        )
    }
#new
async def complete_login(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: Redis,
    response: Response,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 2: Verify login OTP and complete authentication.

    Validation + consumption of the login session token and OTP
    is performed atomically.
    """

    # ---------------------------------------------------------
    # Already logged in
    # ---------------------------------------------------------
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Already logged in",
        )

    # ---------------------------------------------------------
    # Get user
    # ---------------------------------------------------------
    user = await get_user_by_email(db, data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user_id = get_user_id(user)

    login_attempt_key = f"login_attempt:{data.account_token}"
    otp_key = f"otp:{data.otp_code}:{user_id}:login"

    # ---------------------------------------------------------
    # Atomic validate + consume (register_script version)
    # ---------------------------------------------------------
    # Returns:
    #   1  → both keys were valid and have been deleted
    #   0  → login session invalid / expired
    #  -1  → OTP invalid / expired

    lua_script = """
    local stored = redis.call("GET", KEYS[1])
    if (not stored) or (stored \~= ARGV[1]) then
        return 0
    end

    local otp_exists = redis.call("EXISTS", KEYS[2])
    if otp_exists == 0 then
        return -1
    end

    -- Both valid → consume them
    redis.call("DEL", KEYS[1])
    redis.call("DEL", KEYS[2])
    return 1
    """

    script = redis.register_script(lua_script)

    result = await script(
        keys=[login_attempt_key, otp_key],
        args=[str(user_id)],
    )

    if result == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid",
        )

    if result == -1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    # ---------------------------------------------------------
    # Clear login rate limit
    # ---------------------------------------------------------
    await redis.delete(f"login_rate:{user_id}")

    # ---------------------------------------------------------
    # Account status checks
    # ---------------------------------------------------------
    if user.disabled:
        logger.warning(f"Disabled user login attempt: user_id={user_id}")
        return {
            "status": "disabled",
            "message": "Account suspended. Please contact admin.",
            "email": user.email,
        }

    if not user.verified:
        logger.info(f"Unverified user login attempt: user_id={user_id}")
        return {
            "status": "unverified",
            "message": "Account not verified. Please verify your email.",
            "email": user.email,
        }

    # ---------------------------------------------------------
    # Issue tokens
    # ---------------------------------------------------------
    access_token = create_access_token(user_id)
    refresh_token = await create_refresh_token(user_id, redis)
    csrf_token = await generate_csrf_token(user_id, redis)

    set_auth_cookies(
        response=response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )

    logger.info(f"Login successful for user_id={user_id}")

    return {
        "status": "success",
        "message": "Login successful",
    }





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
    current_user: ReadUser = Depends(get_authenticated_user),
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





@router.put(
    "/password",
    status_code=status.HTTP_200_OK,
    summary="Change password",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF on mutations
)
async def update_password(
    data: UpdatePassword,
    db: DBDep,
    current_user: ReadUser = Depends(get_authenticated_user),
) -> dict:
    """Change current user's password."""
    return await change_password(
        data=data,
        db=db,
        current_user=current_user,
    )


@router.delete(
    "/account",
    status_code=status.HTTP_200_OK,
    summary="Delete user account",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF on destructive actions
)
async def delete_user_account(
    data: VerifyPassword,
    response: Response,
    redis: RedisDep,
    db: DBDep,
    current_user: ReadUser = Depends(get_authenticated_user),
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
    r: Any = redis
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
    existing_tokens:set[str] = await r.smembers(f"user_refresh:{user_id}")
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





@router.post(
    "/password/reset-request",
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
    redis: RedisDep,
    db: DBDep,
   
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


