#chartgtp

# =============================================================================
# REGISTER USER
# =============================================================================

async def register_user(
    data: CreateUser,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Register a new user and send a registration OTP.

    Flow:

        1. Block already-authenticated users.
        2. Validate country.
        3. Check email uniqueness.
        4. Create user as unverified.
        5. Commit user to database.
        6. Generate/store hashed registration OTP.
        7. Queue OTP email.
        8. Create anti-replay registration token.
        9. Return registration token.

    Redis:

        otp:{user_id}:registration
            -> SHA-256(OTP)

        reg_attempt:{token}
            -> user_id

    The registration token is created only after OTP generation/storage
    succeeds.
    """

    # =========================================================================
    # 1. Block already-authenticated users
    # =========================================================================

    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Logged in user cannot create account",
        )

    # =========================================================================
    # 2. Validate country
    # =========================================================================

    if data.country_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Country is required",
        )

    country = await db.get(
        Country,
        data.country_id,
    )

    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Country not found",
        )

    # =========================================================================
    # 3. Email uniqueness
    # =========================================================================

    existing = await get_user_by_email(
        db,
        data.email,
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # =========================================================================
    # 4. Create unverified user
    # =========================================================================

    new_user = User(
        surname=data.surname,
        othernames=data.othernames,
        email=data.email,
        hashed_password=hash_password(
            data.password
        ),
        country_id=data.country_id,
        is_admin=False,
        disabled=False,
        verified=False,
        one_click=False,
        payment_id=None,
    )

    db.add(new_user)

    await db.commit()
    await db.refresh(new_user)

    user_id = get_user_id(new_user)

    # =========================================================================
    # 5. Generate + store registration OTP
    #
    # generate_and_send_otp():
    #
    #     otp:{user_id}:registration
    #         -> SHA-256(OTP)
    #
    # The plaintext OTP is only passed to the background email task.
    # =========================================================================

    try:
        await generate_and_send_otp(
            user=new_user,
            otp_type="registration",
            subject="Verify your account",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
        )

    except HTTPException as exc:

        # ---------------------------------------------------------------------
        # OTP rate limit
        #
        # Do not create a registration session because a new OTP was not
        # issued.
        # ---------------------------------------------------------------------

        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise

        logger.error(
            "Failed to send registration OTP for user_id=%s: %s",
            user_id,
            exc.detail,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    except Exception:

        logger.exception(
            "Unexpected error while sending registration OTP "
            "for user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    # =========================================================================
    # 6. Create anti-replay registration token
    #
    # Created ONLY after the OTP was successfully generated and stored.
    # =========================================================================

    reg_token = secrets.token_urlsafe(32)

    reg_key = f"reg_attempt:{reg_token}"

    await redis.set(
        reg_key,
        str(user_id),
        ex=int(
            timedelta(
                minutes=OTP_EXPIRE_MINUTES
            ).total_seconds()
        ),
    )

    logger.info(
        "New registration started for %s (user_id=%s)",
        new_user.email,
        user_id,
    )

    return {
        "message": "OTP sent to your email",
        "email": new_user.email,
        "reg_token": reg_token,
    }


# =============================================================================
# VERIFY REGISTRATION OTP
# =============================================================================

async def verify_registration_otp(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: Redis,
) -> ReadUser:
    """
    Step 2: Verify registration OTP and mark the user as verified.

    Flow:

        1. Find user by email.
        2. Ensure account is not already verified.
        3. Validate reg_attempt:{token}.
        4. Ensure registration token belongs to this user.
        5. Atomically verify + consume OTP.
        6. Delete registration session.
        7. Mark user as verified.
        8. Commit changes.

    Redis:

        reg_attempt:{token}
            -> user_id

        otp:{user_id}:registration
            -> SHA-256(OTP)

    OTP verification is atomic, so the same OTP cannot be successfully
    consumed twice.
    """

    # =========================================================================
    # 1. Get user
    # =========================================================================

    user = await get_user_by_email(
        db,
        data.email,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # =========================================================================
    # 2. Already verified
    # =========================================================================

    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already verified",
        )

    user_id = get_user_id(user)

    # =========================================================================
    # 3. Validate registration session
    # =========================================================================

    reg_key = (
        f"reg_attempt:{data.account_token}"
    )

    stored_id = await redis.get(
        reg_key
    )

    if not stored_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Verification session expired "
                "or invalid"
            ),
        )

    if isinstance(stored_id, bytes):
        stored_id = stored_id.decode("utf-8")

    try:
        stored_user_id = int(stored_id)

    except (TypeError, ValueError):

        await redis.delete(reg_key)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Verification session expired "
                "or invalid"
            ),
        )

    # =========================================================================
    # 4. Ensure registration token belongs to this user
    # =========================================================================

    if stored_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Verification session expired "
                "or invalid"
            ),
        )

    # =========================================================================
    # 5. Atomically verify + consume OTP
    #
    # IMPORTANT:
    #
    # The OTP key is now:
    #
    #     otp:{user_id}:registration
    #
    # NOT:
    #
    #     otp:{otp_code}:{user_id}:registration
    #
    # The submitted OTP is hashed inside verify_and_consume_otp().
    # =========================================================================

    otp_key = (
        f"otp:{user_id}:registration"
    )

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

    # =========================================================================
    # 6. OTP was successfully consumed.
    #
    # Consume the registration session as well.
    # =========================================================================

    await redis.delete(reg_key)

    # =========================================================================
    # 7. Mark account as verified
    # =========================================================================

    user.verified = True
    user.date_verified = datetime.now(
        timezone.utc
    )

    db.add(user)

    # =========================================================================
    # 8. Save changes
    # =========================================================================

    await db.commit()
    await db.refresh(user)

    logger.info(
        "Registration verified successfully for user_id=%s",
        user_id,
    )

    return ReadUser.model_validate(user)




#grok

async def register_user(
    data: CreateUser,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Register a new user (unverified) and send OTP.

    - Blocks already-authenticated users
    - Validates email uniqueness and country
    - Creates the user as unverified
    - Sends OTP (hashed in Redis via generate_and_send_otp)
    - Creates anti-replay registration token only after OTP succeeds
    """
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Logged in user cannot create account",
        )

    # ------------------------------------------------------------------
    # 1. Validate country
    # ------------------------------------------------------------------
    if data.country_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Country is required",
        )

    country = await db.get(Country, data.country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Country not found",
        )

    # ------------------------------------------------------------------
    # 2. Email uniqueness
    # ------------------------------------------------------------------
    existing = await get_user_by_email(db, data.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # ------------------------------------------------------------------
    # 3. Create unverified user
    # ------------------------------------------------------------------
    new_user = User(
        surname=data.surname,
        othernames=data.othernames,
        email=data.email,
        hashed_password=hash_password(data.password),
        country_id=data.country_id,
        is_admin=False,
        disabled=False,
        verified=False,
        one_click=False,
        payment_id=None,
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    user_id = get_user_id(new_user)

    # ------------------------------------------------------------------
    # 4. Send OTP first (stores otp:{user_id}:registration → SHA-256)
    # ------------------------------------------------------------------
    try:
        await generate_and_send_otp(
            user=new_user,
            otp_type="registration",
            subject="Verify your account",
            redis=redis,
            mailer=mailer,
            background_tasks=background_tasks,
        )
    except HTTPException as e:
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise

        logger.error(
            "Failed to send registration OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error while sending registration OTP for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    # ------------------------------------------------------------------
    # 5. Create anti-replay token ONLY after OTP succeeds
    # ------------------------------------------------------------------
    reg_token = secrets.token_urlsafe(32)

    await redis.set(
        f"reg_attempt:{reg_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info(
        "New registration started for %s (user_id=%s)",
        new_user.email,
        user_id,
    )

    return {
        "message": "OTP sent to your email",
        "email": new_user.email,
        "reg_token": reg_token,
    }


# =============================================================================
# VERIFY REGISTRATION OTP
# =============================================================================

async def verify_registration_otp(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: Redis,
) -> ReadUser:
    """
    Step 2: Verify registration OTP and mark the user as verified.

    - Validates anti-replay session token (reg_attempt)
    - Atomically verifies + consumes hashed OTP
      (otp:{user_id}:registration)
    - Marks the user as verified
    """
    # ------------------------------------------------------------------
    # 1. Get user
    # ------------------------------------------------------------------
    user = await get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already verified",
        )

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # 2. Validate registration session (do NOT delete yet)
    # ------------------------------------------------------------------
    reg_key = f"reg_attempt:{data.account_token}"
    stored_id = await redis.get(reg_key)

    if not stored_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid",
        )

    if isinstance(stored_id, bytes):
        stored_id = stored_id.decode()

    try:
        stored_user_id = int(stored_id)
    except (TypeError, ValueError):
        await redis.delete(reg_key)
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
    # 3. Atomically verify + consume hashed OTP
    # ------------------------------------------------------------------
    otp_key = f"otp:{user_id}:registration"

    if not await verify_and_consume_otp(redis, otp_key, data.otp_code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid",
        )

    # ------------------------------------------------------------------
    # 4. Consume registration session
    # ------------------------------------------------------------------
    await redis.delete(reg_key)

    # ------------------------------------------------------------------
    # 5. Mark user as verified
    # ------------------------------------------------------------------
    user.verified = True
    user.date_verified = datetime.now(timezone.utc)

    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info("Registration verified for user_id=%s", user_id)

    return ReadUser.model_validate(user)

