
async def register_user(
    data: CreateUser,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Step 1: Register new user.
    - block user if already logged in
    - Validates email uniqueness
    - Validates country exists
    - Creates user (unverified)
    - Sends OTP via email
    - Returns registration token (anti-replay)
    """
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Logged in user cannot create account"
        )
    # Check email uniqueness
    existing = await get_user_by_email(db, data.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )
    
    # Validate country exists
    country = await db.get(Country, data.country_id)  # ✅ await
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Country not found"
        )
        
    # Enforce user select a country
    if data.country_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="Country is required")
    # Create user
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
    
    # Create registration session token (anti-replay)
    reg_token = secrets.token_urlsafe(32)
    await redis.set(
        f"reg_attempt:{reg_token}",
        str(new_user.id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )
    
    # Send OTP
    await generate_and_send_otp(
        user=new_user,
        otp_type="registration",  # ✅ Fixed typo: otp_yype → otp_type
        subject="Verify your account",
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )
    logger.info(f"New registration started for {new_user.email} (user_id={new_user.id})")
    
    return {
        "message": "OTP sent to your email",
        "email": new_user.email,
        "reg_token": reg_token,
    }


async def verify_registration_otp(
    data: VerifyOtpRequest,
    db: AsyncSession,
    redis: Redis,
) -> ReadUser:
    """
    Step 2: Verify registration OTP.
    
    - Validates session token (anti-replay)
    - Validates OTP
    - Marks user as verified
    """
    # Get user
    user = await get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    if user.verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already verified"
        )
    
    # Validate session token
    stored_id = await redis.get(f"reg_attempt:{data.account_token}")
    if not stored_id or int(stored_id) != user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid"
        )
    await redis.delete(f"reg_attempt:{data.account_token}")
    
    # Validate OTP
    otp_key = f"otp:{data.otp_code}:{user.id}:registration"
    if not await redis.exists(otp_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP expired or invalid"
        )
    await redis.delete(otp_key)
    
    # Mark user as verified
    user.verified = True
    user.date_verified = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return ReadUser.model_validate(user)




#otp


def generate_otp() -> str:
    """Generate a 6-digit OTP."""
    return "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))


# =============================================================================
# OTP
# =============================================================================



async def generate_and_send_otp(
    user: User,
    otp_type: str,
    subject: str,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    override_email:str | None = None,
) -> None:
    """
    Generate OTP, store in Redis FIRST, then queue email delivery.

    Design principle:
        The OTP must be valid and usable the moment this function
        returns, REGARDLESS of whether the email actually arrives.
        Email delivery is best-effort; OTP validity is guaranteed.

        If email delivery fails (even after retries), the user can
        request a resend via the rate-limited resend endpoint - they
        are never stuck waiting on an email that silently failed.

    Args:
        user: The user to send the OTP to
        otp_type: "registration" or "login"
        subject: Email subject line
        redis: Redis client
        mailer: FastMail instance
        background_tasks: FastAPI background tasks queue
        override_email if provided send otp to this email instead of user.email
        used for change flow where OTP goes to New Email

    Raises:
        HTTPException: 429 if rate limit exceeded
    """
    user_id= get_user_id(user)
    # STEP 1: Rate limit check (before generating anything)
    rate_key = f"otp_rate:{user_id}:{otp_type}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, OTP_RATE_WINDOW)
    if count > OTP_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Try again in 1 hour."
        )

    # STEP 2: Generate OTP
    otp = generate_otp()

    # STEP 3: Store in Redis FIRST - this is the source of truth.
    # OTP is valid and verifiable from this point forward,
    # independent of email success/failure.
    otp_key = f"otp:{otp}:{user.id}:{otp_type}"
    await redis.set(
        otp_key,
        "1",
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )
    #send to override email if provided
    recipient_email=override_email if override_email else user.email

    logger.info(
        f"OTP stored in Redis for user {user.id} (type: {otp_type}). "
        f"Queuing email delivery..."
    )
    

    # STEP 4: Queue email delivery (best-effort, non-blocking).
    # If this fails after all tenacity retries inside send_otp_email,
    # the OTP above is STILL valid - user can request a resend.
    background_tasks.add_task(
        send_otp,
        email=recipient_email,
        otp=otp,
        subject=subject,
        otp_type=otp_type,
        mailer=mailer,
    )
    
    
    
    
    
# ______ RESEND OTP _______


async def resend_otp(
    data: ResendOtpRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Resend OTP for an in-progress registration or login flow.
    
    Requires a valid (unexpired) account_token from the original
    register/login request - prevents resending OTPs to arbitrary
    emails without first passing credential validation.
    
    Subject to the same OTP_RATE_LIMIT as the original send.
    """
    user = await get_user_by_email(db, data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Validate the session token is still active
    token_prefix = "reg_attempt" if data.otp_type == "registration" else "login_attempt"
    stored_id = await redis.get(f"{token_prefix}:{data.account_token}")
    if not stored_id or int(stored_id) != user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired - please start over"
        )
    
    subject = (
        "Verify your account" if data.otp_type == "registration"
        else "Your login OTP"
    )
    
    # ✅ Same function - rate limited, stores in Redis before queuing email
    await generate_and_send_otp(
        user=user,
        otp_type=data.otp_type,
        subject=subject,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )
    
    return {"message": "A new OTP has been sent to your email"}
