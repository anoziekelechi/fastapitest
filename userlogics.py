
async def logout_user(
    request: Request,
    response: Response,
    redis: Redis,
    current_user: ReadUser,
) -> dict:
    """
    Logout current device.

    - Revokes the current refresh token
    - Clears auth cookies
    - Removes any leftover OTP keys for this user
    """
    refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if refresh_token:
        await revoke_refresh_token(refresh_token, redis)

    user_id = current_user.id

    # Final OTP key shape: otp:{user_id}:{otp_type}
    otp_keys = [
        f"otp:{user_id}:login",
        f"otp:{user_id}:registration",
        f"otp:{user_id}:email_change",
        f"otp:{user_id}:password_reset",
    ]
    await redis.delete(*otp_keys)

    clear_auth_cookies(response)

    return {"message": "Logged out successfully"}


async def initiate_login(
    data: LoginRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Validate credentials and start login OTP.

    Only verified + active users receive an OTP and login_attempt token.

    Responses:
        status=disabled   → frontend → contact admin (no OTP)
        status=unverified → frontend → resend verification (no OTP)
        status=otp_required → frontend → OTP screen → complete_login
    """
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Already logged in",
        )

    # ------------------------------------------------------------------
    # Credentials (same error → no enumeration)
    # ------------------------------------------------------------------
    user = await get_user_by_email(db, data.email)

    if user is None or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # Account status BEFORE any OTP
    # ------------------------------------------------------------------
    if user.disabled:
        logger.warning("Disabled user login attempt: user_id=%s", user_id)
        return {
            "status": "disabled",
            "message": "Account suspended. Please contact admin.",
            "email": user.email,
        }

    if not user.verified:
        logger.info("Unverified user login attempt: user_id=%s", user_id)
        return {
            "status": "unverified",
            "message": "Account not verified. Please verify your email.",
            "email": user.email,
        }

    # ------------------------------------------------------------------
    # Rate limit (only for users who can log in)
    # ------------------------------------------------------------------
    rate_key = f"login_rate:{user_id}"
    attempts = await redis.incr(rate_key)
    if attempts == 1:
        await redis.expire(
            rate_key,
            int(timedelta(minutes=15).total_seconds()),
        )
    if attempts > 8:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes.",
        )

    # ------------------------------------------------------------------
    # Send login OTP (verified + active only)
    # ------------------------------------------------------------------
    try:
        await generate_and_send_otp(
            user=user,
            otp_type="login",
            subject="Your login OTP",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
        )
    except HTTPException as e:
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(
            "Failed to send login OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error while sending login OTP for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    # ------------------------------------------------------------------
    # Anti-replay session only after OTP is stored
    # ------------------------------------------------------------------
    login_token = secrets.token_urlsafe(32)
    await redis.set(
        f"login_attempt:{login_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info("Login OTP sent to user_id=%s", user_id)

    return {
        "status": "otp_required",
        "message": "OTP sent to your email",
        "email": user.email,
        "login_token": login_token,
    }





async def complete_login(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: Redis,
    response: Response,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 2: Verify login OTP and issue cookies.

    Flow:
        1. Reject already-authenticated users.
        2. Find user.
        3. Validate login attempt session.
        4. Verify current account status.
        5. Atomically verify + consume hashed OTP.
        6. Consume login session.
        7. Clear login rate limit.
        8. Issue access/refresh/CSRF cookies.

    Only reached for users who already passed initiate_login
    (verified + active at that time). Status checks remain as a
    defense-in-depth safety net in case account state changed.
    """

    # ------------------------------------------------------------------
    # 0. Already authenticated
    # ------------------------------------------------------------------
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Already logged in",
        )

    # ------------------------------------------------------------------
    # 1. Find user
    # ------------------------------------------------------------------
    user = await get_user_by_email(db, data.email)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 2. Redis keys
    # ------------------------------------------------------------------
    login_attempt_key = f"login_attempt:{data.account_token}"
    otp_key = f"otp:{user_id}:login"

    # ------------------------------------------------------------------
    # 3. Validate login session
    #
    # Do NOT delete it yet.
    # It is consumed only after successful OTP verification.
    # ------------------------------------------------------------------
    stored_id = await redis.get(login_attempt_key)

    if not stored_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid",
        )

    if isinstance(stored_id, bytes):
        stored_id = stored_id.decode("utf-8")

    try:
        stored_user_id = int(stored_id)

    except (TypeError, ValueError):
        await redis.delete(login_attempt_key)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid",
        )

    if stored_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid",
        )

    # ------------------------------------------------------------------
    # 4. Defense-in-depth account status checks
    #
    # initiate_login() already checked these.
    # We check again because account state may have changed while
    # the OTP was waiting to be verified.
    # ------------------------------------------------------------------
    if user.disabled:
        logger.warning(
            "Disabled user attempted complete_login: user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
        )

    if not user.verified:
        logger.info(
            "Unverified user attempted complete_login: user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified. Please verify your email.",
        )

    # ------------------------------------------------------------------
    # 5. Atomically verify + consume hashed OTP
    #
    # Redis key:
    #     otp:{user_id}:login
    #
    # Redis value:
    #     SHA-256 hash of the OTP
    #
    # Successful verification atomically deletes the OTP.
    # ------------------------------------------------------------------
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
    # 6. Consume login session
    # ------------------------------------------------------------------
    await redis.delete(login_attempt_key)

    # ------------------------------------------------------------------
    # 7. Clear login rate limit
    #
    # The user successfully completed login, so there is no reason
    # to retain the initiate_login OTP-request counter.
    # ------------------------------------------------------------------
    await redis.delete(f"login_rate:{user_id}")

    # ------------------------------------------------------------------
    # 8. Issue authentication tokens
    # ------------------------------------------------------------------
    access_token = create_access_token(user_id)

    refresh_token = await create_refresh_token(
        user_id,
        redis,
    )

    csrf_token = await generate_csrf_token(
        user_id,
        redis,
    )

    # ------------------------------------------------------------------
    # 9. Set authentication cookies
    # ------------------------------------------------------------------
    set_auth_cookies(
        response=response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )

    logger.info(
        "Login successful for user_id=%s",
        user_id,
    )

    return {
        "status": "success",
        "message": "Login successful",
    }



