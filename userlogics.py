
def _fake_reset_response() -> dict:
    """
    Enumeration-safe response for password-reset-request.

    Returns the same shape as a real response but with an
    unusable `reset_token`. Used for unknown or disabled accounts
    so the caller cannot distinguish them from a real one.
    """
    return {
        "message": (
            "If your email is registered, an OTP has been sent. "
            "Enter it below with your new password."
        ),
        "reset_token": secrets.token_urlsafe(32),
        "otp_attempts_used": 1,
        "otp_expires_in_seconds": int(
            timedelta(minutes=settings.otp_expire_minutes).total_seconds()
        ),
    }








async def request_password_reset(
    data: RequestPasswordReset,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Step 1: Request a password reset OTP.

    Enumeration-safe: unknown and disabled accounts receive the same
    response shape as a real one, but with a dummy `reset_token` and
    no OTP sent. The user discovers the truth only on stage 2, where
    a dummy token returns the same "session expired" error as an
    expired real token.
    """
    email = normalize_email(data.email)
    user = await get_user_by_email(db, email)

    # ------------------------------------------------------------------
    # Unknown email — return enumeration-safe response
    # ------------------------------------------------------------------
    if user is None:
        logger.info("Password reset requested for unknown email")
        return _fake_reset_response()

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # Disabled account — same response shape, no OTP sent
    # ------------------------------------------------------------------
    if user.disabled:
        logger.warning(
            "Password reset requested for disabled user_id=%s", user_id
        )
        return _fake_reset_response()

    # ------------------------------------------------------------------
    # Real user — generate + store OTP, create reset session
    # ------------------------------------------------------------------
    try:
        attempts = await generate_and_send_otp(
            user=user,
            otp_type="password_reset",
            subject="Reset your password",
            redis=redis,
            mailer=mailer,
            db=db,
            background_tasks=background_tasks,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(
            "Password reset OTP generation failed for user_id=%s: %s",
            user_id,
            exc.detail,
        )
        return _fake_reset_response()
    except Exception:
        logger.exception(
            "Unexpected password reset OTP generation failure for user_id=%s",
            user_id,
        )
        return _fake_reset_response()

    reset_token = secrets.token_urlsafe(32)
    await redis.set(
        f"reset_attempt:{reset_token}",
        str(user_id),
        ex=int(
            timedelta(minutes=settings.otp_expire_minutes).total_seconds()
        ),
    )

    logger.info(
        "Password reset OTP sent for user_id=%s", user_id
    )

    return {
        "message": (
            "If your email is registered, an OTP has been sent. "
            "Enter it below with your new password."
        ),
        "reset_token": reset_token,
        "otp_attempts_used": attempts,
        "otp_expires_in_seconds": int(
            timedelta(minutes=settings.otp_expire_minutes).total_seconds()
        ),
    }
