
# RESEND OTP FOR USERS WHO HAS REGISTER BEFORE
async def resend_verification(
    data: ResendVerificationRequest,
    db: AsyncSession,
    full_names:str,
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
            full_names=full_names,
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
    }

