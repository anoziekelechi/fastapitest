


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
        await generate_and_send_otp(
            user=user,
            otp_type="password_reset",
            subject="Reset your password",
            redis=redis,
            mailer=mailer,
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
    }


async def reset_password(
    data: ResetPassword,
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







# ============================================================================
# EMAIL CHANGE
# ============================================================================


async def request_email_change(
    data: RequestEmailChange,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser,
) -> dict:
    """
    Step 1: Verify the current password and send an OTP to the NEW email.

    Flow:
        1. Require authentication.
        2. Fetch the real ORM user.
        3. Verify the current password.
        4. Normalize the new email.
        5. Ensure the new email differs from the current email.
        6. Ensure the new email is not already registered.
        7. Create an email-change session token.
        8. Store user_id + new_email in Redis.
        9. Generate/store the email-change OTP.
        10. Queue the OTP email to the NEW email.

    Redis:

        Session:
            email_change:{token}
                -> JSON:
                   {
                       "user_id": user_id,
                       "new_email": "new@example.com"
                   }

        OTP:
            otp:{user_id}:email_change

        OTP rate limit:
            otp_rate:{user_id}:email_change

    Security:
        - Requires the current authenticated session.
        - Requires the current password.
        - OTP is sent to the new email address.
        - New email is stored server-side and is not trusted from
          the verification request.
        - A new OTP overwrites the previous email-change OTP.
    """

    # ------------------------------------------------------------------
    # 1. Fetch the real ORM user
    #
    # current_user is a ReadUser schema, not the ORM object.
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
    # 2. Verify current password
    # ------------------------------------------------------------------
    if not verify_password(
        data.current_password,
        user.hashed_password,
    ):
        logger.warning(
            "Failed password verification for email change: "
            "user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    # ------------------------------------------------------------------
    # 3. Normalize the new email
    #
    # This keeps comparison, uniqueness checks, Redis storage, and the
    # eventual database value consistent.
    # ------------------------------------------------------------------
    new_email = normalize_email(data.new_email)

    if new_email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid new email address is required",
        )

    # ------------------------------------------------------------------
    # 4. Ensure the new email differs from the current email
    # ------------------------------------------------------------------
    if new_email == user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New email must be different from your current email",
        )

    # ------------------------------------------------------------------
    # 5. Ensure the new email is not already registered
    # ------------------------------------------------------------------
    existing = await get_user_by_email(
        db,
        new_email,
    )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This email address is already registered "
                "to another account"
            ),
        )

    # ------------------------------------------------------------------
    # 6. Create email-change session token
    # ------------------------------------------------------------------
    email_change_token = secrets.token_urlsafe(32)

    email_change_key = (
        f"email_change:{email_change_token}"
    )

    session_ttl = int(
        timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
    )

    # ------------------------------------------------------------------
    # 7. Store user_id + new_email in Redis
    #
    # JSON is preferable to:
    #
    #     f"{user_id}:{new_email}"
    #
    # because JSON provides explicit fields and avoids delimiter
    # parsing issues.
    # ------------------------------------------------------------------
    session_data = json.dumps(
        {
            "user_id": user_id,
            "new_email": new_email,
        }
    )

    try:
        await redis.set(
            email_change_key,
            session_data,
            ex=session_ttl,
        )

    except Exception as exc:
        logger.exception(
            "Failed to create email-change session "
            "for user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to start email change. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 8. Generate + store OTP and queue email
    #
    # generate_and_send_otp():
    #
    #     otp:{user_id}:email_change
    #
    # The OTP is hashed before being stored in Redis.
    # The plaintext OTP is only passed to the email background task.
    # ------------------------------------------------------------------
    try:
        await generate_and_send_otp(
            user=user,
            otp_type="email_change",
            subject="Verify your new email address",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
            override_email=new_email,
        )

    except HTTPException as exc:
        # The email-change session should not remain if OTP generation
        # itself failed.
        await redis.delete(email_change_key)

        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise

        logger.error(
            "Email-change OTP generation failed "
            "for user_id=%s: %s",
            user_id,
            exc.detail,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        ) from exc

    except Exception as exc:
        await redis.delete(email_change_key)

        logger.exception(
            "Unexpected email-change OTP generation failure "
            "for user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 9. Success
    # ------------------------------------------------------------------
    logger.info(
        "Email-change OTP generated for user_id=%s",
        user_id,
    )

    return {
        "message": (
            "An OTP has been sent to your new email address. "
            "Please verify it to complete the email change."
        ),
        "email_change_token": email_change_token,
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
    email_change_key = (
        f"email_change:{data.email_change_token}"
    )

    stored_data = await redis.get(
        email_change_key,
    )

    if not stored_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Email change session expired or invalid. "
                "Please request a new OTP."
            ),
        )

    if isinstance(stored_data, bytes):
        stored_data = stored_data.decode("utf-8")

    # ------------------------------------------------------------------
    # 3. Parse session data
    # ------------------------------------------------------------------
    try:
        session_data = json.loads(stored_data)

        stored_user_id = int(
            session_data["user_id"]
        )

        new_email = normalize_email(
            session_data["new_email"]
        )

    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        # Corrupted session data should not remain in Redis.
        await redis.delete(email_change_key)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid email-change session. "
                "Please request a new OTP."
            ),
        )

    if new_email is None:
        await redis.delete(email_change_key)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid email-change session. "
                "Please request a new OTP."
            ),
        )

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
        await redis.delete(email_change_key)

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
        await redis.delete(email_change_key)

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
        await redis.delete(email_change_key)

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


