
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








async def initiate_login(
    data: LoginRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Validate credentials, send OTP.
    Allows disabled and unverified users through to give specific messages.
    """
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Already logged in"
        )

    # Validate credentials only (same error - prevents enumeration)
    user = await get_user_by_email(db, data.email)
    if user is None or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    # ✅ Removed: disabled and verified checks
    # They now get specific messages via complete_login
    # which checks status AFTER OTP verification

    user_id = get_user_id(user)

    # Rate limiting
    rate_key = f"login_rate:{user_id}"
    r: Any = redis
    attempts = await r.incr(rate_key)
    if attempts == 1:
        await r.expire(
            rate_key,
            int(timedelta(minutes=15).total_seconds())
        )
    if attempts > 8:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes."
        )

    # Anti-replay token
    login_token = secrets.token_urlsafe(32)
    await r.set(
        f"login_attempt:{login_token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    # Send OTP
    await generate_and_send_otp(
        user=user,
        otp_type="login",
        subject="Your login OTP",
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )

    logger.info(f"Login OTP sent to user_id={user_id}")

    return {
        "message": "OTP sent to your email",
        "email": user.email,
        "login_token": login_token,
    }
    
