

async def request_password_reset(
    data: RequestPasswordReset,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    email = normalize_email(data.email)
    user = await get_user_by_email(db, email)

    generic_response = {
        "message": (
            "If your email is registered, an OTP has been sent. "
            "Enter it below with your new password."
        ),
    }

    # Unknown email → return generic response with dummy values.
    # Frontend continues to stage 2 with a token that will fail
    # validation there (same error as an expired session).
    if user is None:
        dummy_token = secrets.token_urlsafe(32)
        return {
            **generic_response,
            "reset_token": dummy_token,
            "otp_attempts_used": 1,
            "otp_expires_in_seconds": int(
                timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
            ),
        }

    user_id = get_user_id(user)

    # Optional: silently bail if disabled (don't leak state either)
    if user.disabled:
        logger.warning(
            "Password reset requested for disabled user_id=%s", user_id
        )
        dummy_token = secrets.token_urlsafe(32)
        return {
            **generic_response,
            "reset_token": dummy_token,
            "otp_attempts_used": 1,
            "otp_expires_in_seconds": int(
                timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
            ),
        }

    # Send real OTP
    attempts = await generate_and_send_otp(
        user=user,
        otp_type="password_reset",
        subject="Reset your password",
        redis=redis,
        mailer=mailer,
        db=db,
        background_tasks=background_tasks,
    )

    reset_token = secrets.token_urlsafe(32)
    await redis.set(
        f"reset_attempt:{reset_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    return {
        **generic_response,
        "reset_token": reset_token,
        "otp_attempts_used": attempts,
        "otp_expires_in_seconds": int(
            timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
        ),
    }
