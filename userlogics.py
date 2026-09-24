async def request_email_change(
    data: RequestEmailChangeRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser,
) -> dict:
    """
    Stage 2: Consume the approval OTP and submit the new email.
    """

    # ------------------------------------------------------------------
    # 1. Load user
    # ------------------------------------------------------------------
    user = await get_user_by_id(db, current_user.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 2. Validate email_approval session
    # ------------------------------------------------------------------
    approval_key = f"email_approval:{data.email_approval_token}"
    stored = await redis.get(approval_key)

    if not stored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approval session expired or invalid. Please start over.",
        )

    if isinstance(stored, bytes):
        stored = stored.decode("utf-8")

    try:
        stored_user_id = int(stored)
    except (TypeError, ValueError):
        await redis.delete(approval_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approval session expired or invalid. Please start over.",
        )

    if stored_user_id != user_id:
        logger.warning(
            "email_approval token mismatch: token_owner=%s, requester=%s",
            stored_user_id,
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approval session expired or invalid. Please start over.",
        )

    # ------------------------------------------------------------------
    # 3. Account-state re-check
    # ------------------------------------------------------------------
    if user.disabled:
        await redis.delete(approval_key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
        )
    if not user.verified:
        await redis.delete(approval_key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified. Please verify your email first.",
        )

    # ------------------------------------------------------------------
    # 4. Validate new email BEFORE consuming the approval OTP,
    #    so a typo doesn't cost the user their approval code.
    # ------------------------------------------------------------------
    new_email = normalize_email(data.new_email)
    if not new_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid new email address is required",
        )

    if new_email == user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New email must be different from your current email",
        )

    existing = await get_user_by_email(db, new_email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This email address is already registered "
                "to another account"
            ),
        )

    # ------------------------------------------------------------------
    # 5. PRE-FLIGHT OTP COOLDOWN CHECK
    #
    # If the email-change OTP rate window for this user is already
    # exhausted, we must abort BEFORE consuming the approval OTP.
    # Otherwise the user loses their approval code and must restart
    # from stage 1.
    # ------------------------------------------------------------------
    email_change_rate_key = f"otp_rate:{user_id}:email_change"
    used_raw = await redis.get(email_change_rate_key)
    rate_ttl = await redis.ttl(email_change_rate_key)

    if used_raw is not None:
        try:
            used = int(used_raw)
        except (TypeError, ValueError):
            used = 0

        if (
            used >= settings.otp_rate_limit
            and isinstance(rate_ttl, int)
            and rate_ttl > 0
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Too many OTP requests for this operation. "
                    "Please try again later."
                ),
                headers={"Retry-After": str(rate_ttl)},
            )

    # ------------------------------------------------------------------
    # 6. Atomically verify + consume approval OTP
    # ------------------------------------------------------------------
    approval_otp_key = f"otp:{user_id}:email_change_approval"
    otp_valid = await verify_and_consume_otp(
        redis=redis,
        otp_key=approval_otp_key,
        submitted_otp=data.otp_code,
    )
    if not otp_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    # ------------------------------------------------------------------
    # 7. Consume the approval session — its purpose is fulfilled
    # ------------------------------------------------------------------
    try:
        await redis.delete(approval_key)
    except Exception:
        logger.exception(
            "Failed to delete email_approval session for user_id=%s",
            user_id,
        )

    # ------------------------------------------------------------------
    # 8. Send OTP to the NEW email
    # ------------------------------------------------------------------
    try:
        attempts = await generate_and_send_otp(
            user=user,
            otp_type="email_change",
            subject="Verify your new email address",
            redis=redis,
            mailer=mailer,
            db=db,
            background_tasks=background_tasks,
            override_email=new_email,
        )
    except HTTPException as e:
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(
            "Failed to send email_change OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error sending email_change OTP for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    # ------------------------------------------------------------------
    # 9. Create email_change session
    # ------------------------------------------------------------------
    change_token = secrets.token_urlsafe(32)
    session_data = json.dumps(
        {"user_id": user_id, "new_email": new_email}
    )
    await redis.set(
        f"email_change:{change_token}",
        session_data,
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info(
        "Email change OTP sent to new email for user_id=%s (target=%s)",
        user_id,
        new_email,
    )

    return {
        "message": (
            "An OTP has been sent to your new email address. "
            "Please verify it to complete the email change."
        ),
        "email_change_token": change_token,
        "otp_attempts_used": attempts,
        "otp_expires_in_seconds": int(
            timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
        ),
    }







class ResendVerificationResponse(BaseModel):
    """
    Response for POST /auth/resend-verification.

    Fields other than `message` are optional because the endpoint
    intentionally returns a generic response when the email doesn't
    exist or the account is disabled (enumeration protection).
    """
    message: str
    email: EmailStr | None = None
    reg_token: str | None = None
    otp_attempts_used: int | None = None
    otp_expires_in_seconds: int | None = None






