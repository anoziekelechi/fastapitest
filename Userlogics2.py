
# app/services/auth_service.py

async def approve_email_change(
    data: ApproveEmailChangeRequest,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
    current_user: ReadUser,
) -> dict:
    """
    Stage 1: Verify current password, send OTP to the CURRENT email,
    and issue an approval session token.

    Flow:
        1. Load ORM user.
        2. Verify current password.
        3. Reject if disabled / unverified.
        4. Send OTP to user.email  (otp_type="email_change_approval").
        5. Create email_approval:{token} → user_id.

    On OTP send failure the session token is NOT created.
    """
    # 1. Load user
    user = await get_user_by_id(db, current_user.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_id = get_user_id(user)

    # 2. Verify current password
    if not verify_password(data.current_password, user.hashed_password):
        logger.warning(
            "Failed password verification for email change approval: user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    # 3. Account-state guards
    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
        )
    if not user.verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified. Please verify your email first.",
        )

    # 4. Send OTP to CURRENT email
    try:
        attempts = await generate_and_send_otp(
            user=user,
            otp_type="email_change_approval",
            subject="Approve your email change",
            redis=redis,
            mailer=mailer,
            db=db,
            background_tasks=background_tasks,
        )
    except HTTPException as e:
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise
        logger.error(
            "Failed to send email_change_approval OTP for user_id=%s: %s",
            user_id,
            e.detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )
    except Exception:
        logger.exception(
            "Unexpected error sending email_change_approval OTP for user_id=%s",
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP. Please try again.",
        )

    # 5. Session token (only after OTP stored)
    token = secrets.token_urlsafe(32)
    await redis.set(
        f"email_approval:{token}",
        str(user_id),
        ex=int(timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()),
    )

    logger.info(
        "Email change approval OTP sent for user_id=%s", user_id
    )

    return {
        "message": (
            "An OTP to approve your change of email "
            "has been sent to your inbox."
        ),
        "email_approval_token": token,
        "otp_attempts_used": attempts,
        "otp_expires_in_seconds": int(
            timedelta(minutes=OTP_EXPIRE_MINUTES).total_seconds()
        ),
    }


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

    Flow:
        1. Load ORM user.
        2. Validate email_approval session (belongs to caller).
        3. Re-check account state (disabled / verified).
        4. Validate the new email:
             - normalize
             - must differ from current email
             - must not be already registered
           (Done BEFORE consuming the OTP so a bad email doesn't
            cost the user their approval code.)
        5. Atomically verify + consume otp:{user_id}:email_change_approval.
        6. Consume email_approval session.
        7. Send a fresh OTP to the NEW email (otp_type="email_change").
        8. Create email_change:{token} → { user_id, new_email }.
        9. Return new-email token + OTP meta.
    """

    # 1. Load user
    user = await get_user_by_id(db, current_user.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    user_id = get_user_id(user)

    # 2. Validate email_approval session
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

    # 3. Account-state re-check (may have flipped since stage 1)
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

    # 4. Validate new email FIRST (so a bad email doesn't consume the OTP)
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

    # 5. Atomically verify + consume approval OTP
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

    # 6. Consume the approval session — its purpose is fulfilled
    try:
        await redis.delete(approval_key)
    except Exception:
        logger.exception(
            "Failed to delete email_approval session for user_id=%s",
            user_id,
        )

    # 7. Send OTP to the NEW email
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

    # 8. Create email_change session
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






# app/routers/users.py

from app.schemas.user import (
    ApproveEmailChangeRequest,
    ApproveEmailChangeResponse,
    RequestEmailChangeRequest,
    RequestEmailChangeResponse,
    VerifyEmailChange,
    ReadUser,
)
from app.services.auth_service import (
    approve_email_change,
    request_email_change,
    verify_new_email,
)


@router.post(
    "/me/email/approve",
    response_model=ApproveEmailChangeResponse,
    status_code=status.HTTP_200_OK,
)
async def approve_email_change_route(
    data: ApproveEmailChangeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    mailer=Depends(get_mailer),
    current_user: ReadUser = Depends(get_current_user),
):
    return await approve_email_change(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/me/email/request",
    response_model=RequestEmailChangeResponse,
    status_code=status.HTTP_200_OK,
)
async def request_email_change_route(
    data: RequestEmailChangeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    mailer=Depends(get_mailer),
    current_user: ReadUser = Depends(get_current_user),
):
    return await request_email_change(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/me/email/verify",
    response_model=ReadUser,
    status_code=status.HTTP_200_OK,
)
async def verify_new_email_route(
    data: VerifyEmailChange,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: ReadUser = Depends(get_current_user),
):
    return await verify_new_email(
        data=data,
        db=db,
        redis=redis,
        current_user=current_user,
    )
