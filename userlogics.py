async def initiate_login(
    data: LoginRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Validate credentials and send login OTP.

    Notes:
    - Disabled and unverified users are intentionally allowed
      to reach complete_login so the frontend can show
      specific status messages.
    - The anti-replay login token is created only after
      the OTP has been successfully generated.
    """

    # ------------------------------------------------------------------
    # 1. Already logged in
    # ------------------------------------------------------------------
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Already logged in",
        )

    # ------------------------------------------------------------------
    # 2. Validate credentials (same error → prevents enumeration)
    # ------------------------------------------------------------------
    user = await get_user_by_email(db, data.email)

    if user is None or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 3. Rate limiting
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
    # 4. Send OTP first
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
        # Re-raise rate-limit errors from the OTP helper
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
    # 5. Create anti-replay login token ONLY after OTP succeeds
    # ------------------------------------------------------------------
    login_token = secrets.token_urlsafe(32)

    await redis.set(
        f"login_attempt:{login_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info("Login OTP sent to user_id=%s", user_id)

    return {
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

    lua_script = r"""
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






