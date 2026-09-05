
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
    #       otp:{user_id}:password_reset
    #
    #   OTP rate limits:
    #       otp_rate:{user_id}:registration
    #       otp_rate:{user_id}:login
    #       otp_rate:{user_id}:email_change
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
        f"otp_rate:{user_id}:password_reset",

        # OTP hashes
        f"otp:{user_id}:registration",
        f"otp:{user_id}:login",
        f"otp:{user_id}:email_change",
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





