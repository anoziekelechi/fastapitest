@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    summary="Rotate tokens",
    response_description="New auth cookies set",
)
async def refresh_tokens(
    request: Request,
    response: Response,
    redis: RedisDep,
) -> dict:
    """
    Rotate access + refresh tokens.
    """
    # Get refresh token from HttpOnly cookie
    refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active session"
        )
    
    # Validate and get user_id
    user_id = await validate_refresh_token(
        refresh_token=refresh_token,
        redis=redis,
    )
    
   
    
    # Issue new tokens
    tokens = await create_token_response(
        user_id=user_id,
        redis=redis,
    )
    
    # ✅ Set as cookies only - never in body
    set_auth_cookies(
        response=response,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        csrf_token=tokens.csrf_token,
    )
    
    return {"message": "Tokens refreshed"}


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Logout user",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF protection on logout
)
async def logout(
    request: Request,
    response: Response,
    redis: RedisDep,
    current_user: CurrentUser,
) -> dict:
    """
    Logout current user.
    
    - Validates CSRF token
    - Revokes refresh token
    - Clears all auth cookies
    - Cleans up Redis data
    """
    return await logout_user(
        request=request,
        response=response,
        redis=redis,
        current_user=current_user,
    )



@router.patch(
    "/update/names",
    response_model=UpdateNamesResponse,
    status_code=status.HTTP_200_OK,
    summary="Update user names",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF on mutations
)
async def update_names(
    data: UpdateNames,
    db: DBDep,
    current_user: CurrentUser,
):
    """Update user's surname and/or othernames."""
    return await update_user_names(
        data=data,
        db=db,
        current_user=current_user,
    )


@router.delete(
    "/delete/account",
    status_code=status.HTTP_200_OK,
    summary="Delete user account",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF on destructive actions
)
async def delete_user_account(
    data: VerifyPassword,
    response: Response,
    redis: RedisDep,
    db: DBDep,
    current_user: CurrentUser,
) -> dict:
    """
    Permanently delete user account.
    
    - Requires password confirmation
    - Cleans up all user data in Redis
    - Clears auth cookies
    """
    return await delete_account(
        data=data,
        db=db,
        redis=redis,
        response=response,
        current_user=current_user,
    )


#_____ OTP SEND AND RESEND _________

@router.post(
    "/otp/resend",
    response_model=ResendOtpResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend OTP for registration or login",
)
async def resend_otp_route(
    data: ResendOtpRequest,
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,
    db: DBDep,
) -> dict:
    """
    Resend OTP if the original email didn't arrive.
    
    Rate limited to 5 requests per hour per user per flow type.
    """
    return await resend_otp(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )


@router.post(
    "/password/reset/request",
    response_model=RequestPasswordResetResponse,
    status_code=status.HTTP_200_OK,
    summary="Request password reset OTP",
)
async def request_password_reset(
    data: RequestResetPassword,
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,
    db: DBDep,
    # ✅ Optional - not logged in is expected here
    current_user: OptionalCurrentUser,
) -> dict:
    """
    Request a password reset OTP.

    Blocked if already authenticated (use 'change password' instead).
    Returns the same response whether email exists or not (prevents enumeration).
    """
    return await request_reset_password(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/password/reset/verify",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Complete password reset with OTP",
)
async def complete_password_reset(
    data: ConfirmPasswordReset,
    redis: RedisDep,
    db: DBDep,
   
    # ✅ Optional - not logged in is expected here
    current_user: OptionalCurrentUser,
) -> dict:
    """
    Complete password reset.

    Requires OTP from reset-request step and the reset_token
    returned in that response (anti-replay protection).

    Invalidates all existing sessions on success (forces re-login).
    """
    return await reset_password(
        data=data,
        db=db,
        redis=redis,
        current_user=current_user,
)


# Change Email


@router.post(
    "/email/approve",
    response_model=ApproveEmailChangeResponse,
    status_code=status.HTTP_200_OK,
)
async def approve_email_change_route(
    data: ApproveEmailChangeRequest,
    background_tasks: BackgroundTasks,
    db: DBDep,
    redis: RedisDep,
    mailer:MailDep,
    current_user: CurrentUser,
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
    "/email/request",
    response_model=RequestEmailChangeResponse,
    status_code=status.HTTP_200_OK,
)
async def request_email_change_route(
    data: RequestEmailChangeRequest,
    background_tasks: BackgroundTasks,
    db:DBDep,
    redis: RedisDep,
    mailer:MailDep,
    current_user: CurrentUser,
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
    "/email/verify",
    response_model=VerifyEmailChangeResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_new_email_route(
    data: VerifyEmailChange,
    db: DBDep,
    redis: RedisDep,
    current_user: CurrentUser,
):
    return await verify_new_email(
        data=data,
        db=db,
        redis=redis,
        current_user=current_user,
    )


# CONTACT ADMIN

@router.post(
    "/contact-admin",
    response_model=ContactAdminResponse,
    status_code=status.HTTP_200_OK,
    summary="Contact support (disabled accounts only)",
)
async def contact_admin_route(
    data: ContactAdminMessage,
    background_tasks: BackgroundTasks,
    db: DBDep,
    redis: RedisDep,
    mailer: MailDep,
) -> dict:
    """
    Disabled users can send a message to support.
    Message goes to country.email_support or settings.mail_username.
    Rate limited to 3 requests per hour.
    """
    return await handle_disabled_account(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )
    
    
    #================================================
    # RESEND OTP FOR UNVERIFIED USERS
    
@router.post(
    "/resend/verification",
    response_model=ResendVerificationResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend verification OTP (unverified accounts)",
)
async def resend_verification_route(
    data: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    db: DBDep,
    redis: RedisDep,
    mailer: MailDep,
) -> dict:
    """
    Resend verification OTP to unverified users.
    Requires account_token from login response.
    """
    return await resend_verification(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
    )


#==============================================================
# PROFILE

@router.get(
    "/userprofile",
    status_code=status.HTTP_200_OK,
    summary="Get current user profile",
    response_model=UserProfile,
    response_model_exclude_none=True,   # ✅ Hides None fields from response
)
async def get_profile(
    db: DBDep,
    current_user: CurrentUser
) -> dict:
    """
    Get authenticated user profile.

    Response varies by role:
        Regular user: base fields + group info if assigned
        Admin: base fields + group info + is_admin: true
    """
    return await get_user_profile(
        db=db,
        current_user=current_user,
    )

@router.post(
    "/password/request",
    response_model=RequestPasswordChangeResponse,
    status_code=status.HTTP_200_OK,
)
async def request_password_change_route(
    data: RequestPasswordChange,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    db: DBDep,
    redis: RedisDep,
    mailer:MailDep,
):
    return await request_password_change(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/password/confirm",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def confirm_password_change_route(
    data: ConfirmPasswordChange,
    db: DBDep,
    redis: RedisDep,
    current_user: CurrentUser,
):
    return await confirm_password_change(
        data=data,
        db=db,
        redis=redis,
        current_user=current_user,
    )





# =============================================================================
# ADMIN-ONLY READ
# =============================================================================

@router.get(
    "",
    response_model=FirmListRead,
    status_code=status.HTTP_200_OK,
    summary="List all firms (admin only)",
)
async def list_all_firms(
    db: DBDep,
    admin: AdminUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_firms(db=db, skip=skip, limit=limit)


# =============================================================================
# PUBLIC-ISH READ — OWNER OR ADMIN
#
# Not truly public: the service layer enforces ownership. But the
# route itself is available to any authenticated user so non-owners
# get a proper 403 instead of an ambiguous 404.
# =============================================================================

@router.get(
    "/{slug}",
    response_model=FirmRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single firm (owner or admin)",
)
async def get_firm(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_single_firm(
        slug=slug, db=db, current_user=current_user
    )


# =============================================================================
# AUTHENTICATED MUTATIONS — OWNER OR ADMIN
# =============================================================================

@router.post(
    "",
    response_model=CreateFirmResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a firm (authenticated)",
)
async def create(
    data: FirmCreate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await create_firm(data=data, db=db, current_user=current_user)


@router.patch(
    "/{slug}",
    response_model=UpdateFirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a firm (owner or admin)",
)
async def patch_firm(
    slug: str,
    data: FirmUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_firm(
        slug=slug, data=data, db=db, current_user=current_user
    )


@router.delete(
    "/{slug}",
    response_model=DeleteFirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a firm (owner or admin) — cascades receipts",
)
async def remove_firm(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await delete_firm(
        slug=slug, db=db, current_user=current_user
 )


