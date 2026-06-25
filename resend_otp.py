
class ResendOtpRequest(BaseModel):
    """Schema for resending an OTP."""
    model_config = ConfigDict(extra="forbid")
    
    email: EmailStr
    account_token: str = Field(..., min_length=32)  # Same anti-replay token from step 1
    otp_type: Literal["registration", "login"]
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()



#logics

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


#routes
@router.post(
    "/otp/resend",
    status_code=status.HTTP_200_OK,
    summary="Resend OTP for registration or login",
)
async def resend_otp_route(
    data: ResendOtpRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    redis: RedisDep = Depends(),
    mailer: MailDep = Depends(),
) -> dict:
    """
    Resend OTP if the original email didn't arrive.
    
    Rate limited to 5 requests per hour per user per flow type.
    """
    from api.users.logics import resend_otp
    return await resend_otp(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )

