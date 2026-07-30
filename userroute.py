#api/user/routes.py

from redis.asyncio import Redis
from fastapi_mail import FastMail
import jwt
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from regex import D
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import DBDep
from api.core.redis import RedisDep
from api.core.mail import MailDep
from api.core.auth import (
    validate_refresh_token,
    revoke_refresh_token,
    create_token_response,
    set_auth_cookies,
    csrf_protection,
    REFRESH_TOKEN_COOKIE,
    
)
from api.core.settings import get_settings
from api.users.logics import (
    register_user,
    verify_registration_otp,
    initiate_login,
    complete_login,
    logout_user,
    get_optional_user,
    get_authenticated_user,
    change_password,
    delete_account,
    update_user_names,
    request_email_change,
    verify_new_email,
    request_reset_password,
    resend_otp,
    reset_password,
    
)
from api.users.schemas import (
    CreateUser,
    LoginRequest,
    VerifyOtpRequest,
    UpdateNames,
    UpdatePassword,
    VerifyPassword,
    ReadUser,
    VerifyEmailChange,
    RequestEmailChange,
    RequestResetPassword,
    ResendOtpRequest,
    ResetPassword,
)


settings = get_settings()

router = APIRouter(prefix="/auth", tags=["Authentication"])




async def require_csrf(
    request: Request,
    redis: Redis,
    current_user: ReadUser = Depends(get_authenticated_user),
) -> None:  #get_authenticated_user
    """
    Dependency: Require valid CSRF token.
    
    Usage:
        @router.post("/sensitive")
        async def sensitive(_: None = Depends(require_csrf)):
            ...
    """
    await csrf_protection(
        request=request,
        redis=redis,
        user_id=current_user.id,
    )

# =============================================================================
# REGISTRATION
# =============================================================================
@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register new user account",
    response_description="OTP sent to email",
)
async def register(
    data: CreateUser,
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,   # ✅ No Depends() needed
    db: DBDep,
    current_user:ReadUser | None = Depends(get_optional_user),
) -> dict:
    """
    Step 1: Register a new user.
    
    - Validates email uniqueness
    - Validates country exists
    - Creates unverified account
    - Sends OTP to email
    
    Returns registration token for OTP verification step.
    """
    return await register_user(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user
    )


@router.post(
    "/register/verify",
    status_code=status.HTTP_200_OK,
    response_model=ReadUser,
    summary="Verify registration OTP",
)
async def verify_registration(
    data: VerifyOtpRequest,
    redis: RedisDep,
    db: DBDep,

) -> ReadUser:
    """
    Step 2: Verify registration OTP.
    
    - Validates session token (anti-replay)
    - Validates OTP
    - Activates account
    
    Returns activated user profile.
    """
    return await verify_registration_otp(
        data=data,
        db=db,
        redis=redis,
    )


# =============================================================================
# LOGIN (2FA)
# =============================================================================

@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    summary="Login step 1 - validate credentials",
    response_description="OTP sent to email",
)
async def login(
    data: LoginRequest,
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,   # ✅ No Depends() needed
    db: DBDep,
    current_user:ReadUser | None = Depends(get_optional_user)
) -> dict:
    """
    Step 1: Validate credentials and send OTP.
    
    - Validates email + password
    - Rate limits attempts (max 8 per 15 minutes)
    - Sends OTP via email
    
    Returns login token for OTP verification step.
    """
    return await initiate_login(
        data=data,
        db=db,
        redis=redis,
        mailer=mailer,
        background_tasks=background_tasks,
        current_user=current_user,
    )


@router.post(
    "/login/verify",
    status_code=status.HTTP_200_OK,
    summary="Login step 2 - verify OTP",
    response_description="Auth cookies set",
)
async def verify_login(
    redis: RedisDep,
    data: VerifyOtpRequest,
    response: Response,                  # ✅ FastAPI injects this
    db: DBDep,
    
) -> dict:
    """
    Step 2: Verify OTP and issue tokens.
    
    - Validates session token (anti-replay)
    - Validates OTP
    - Issues tokens as HttpOnly cookies
    
    ✅ Tokens NEVER in response body - cookies only!
    """
    return await complete_login(
        data=data,
        db=db,
        redis=redis,
        response=response,    # ← Logic sets cookies via response
    )


# =============================================================================
# TOKEN MANAGEMENT
# =============================================================================

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
    
    - Validates refresh token cookie
    - Revokes old refresh token (rotation)
    - Issues new tokens as HttpOnly cookies
    
    Called automatically by frontend when access token expires.
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
    
    # Revoke old token (rotation - prevents reuse)
    await revoke_refresh_token(
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
    current_user: ReadUser = Depends(get_authenticated_user),
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


# =============================================================================
# PROFILE
# =============================================================================

@router.get(
    "/profile",
    response_model=ReadUser,
    status_code=status.HTTP_200_OK,
    summary="Get current user profile",
)
async def get_profile(
    current_user: ReadUser = Depends(get_authenticated_user),
) -> ReadUser:
    """Get authenticated user's profile."""
    return current_user


@router.put(
    "/profile/names",
    response_model=ReadUser,
    status_code=status.HTTP_200_OK,
    summary="Update user names",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF on mutations
)
async def update_names(
    data: UpdateNames,
    db: DBDep,
    current_user: ReadUser = Depends(get_authenticated_user),
) -> ReadUser:
    """Update user's surname and/or othernames."""
    return await update_user_names(
        data=data,
        db=db,
        current_user=current_user,
    )


@router.put(
    "/password",
    status_code=status.HTTP_200_OK,
    summary="Change password",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF on mutations
)
async def update_password(
    data: UpdatePassword,
    db: DBDep,
    current_user: ReadUser = Depends(get_authenticated_user),
) -> dict:
    """Change current user's password."""
    return await change_password(
        data=data,
        db=db,
        current_user=current_user,
    )


@router.delete(
    "/account",
    status_code=status.HTTP_200_OK,
    summary="Delete user account",
    dependencies=[Depends(require_csrf)],  # ✅ CSRF on destructive actions
)
async def delete_user_account(
    data: VerifyPassword,
    response: Response,
    redis: RedisDep,
    db: DBDep,
    current_user: ReadUser = Depends(get_authenticated_user),
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
    "/password/reset-request",
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
    current_user: ReadUser | None = Depends(get_optional_user),
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
    "/password/reset",
    status_code=status.HTTP_200_OK,
    summary="Complete password reset with OTP",
)
async def complete_password_reset(
    data: ResetPassword,
    redis: RedisDep,
    db: DBDep,
   
    # ✅ Optional - not logged in is expected here
    current_user: ReadUser | None = Depends(get_optional_user),
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
    "/email/change-request",
    status_code=status.HTTP_200_OK,
    summary="Request email change - sends OTP to new email",
    dependencies=[Depends(require_csrf)],   # ✅ CSRF on sensitive mutations
)
async def request_email_change_route(
    data: RequestEmailChange,
    redis: RedisDep,
    mailer: MailDep,
    background_tasks: BackgroundTasks,
    db: DBDep,
    current_user: ReadUser = Depends(get_authenticated_user),  # ✅ Required (not optional)
) -> dict:
    """
    Request an email address change.

    - Requires authentication
    - Requires CSRF token
    - Verifies current password before proceeding
    - Sends OTP to the NEW email address (proves ownership)
    - Returns email_change_token needed for verify step
    """
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
    response_model=ReadUser,
    status_code=status.HTTP_200_OK,
    summary="Verify new email OTP and complete email change",
    dependencies=[Depends(require_csrf)],   # ✅ CSRF on mutations
)
async def verify_new_email_route(
    data: VerifyEmailChange,
    redis: RedisDep,
    db: DBDep,
    current_user: ReadUser = Depends(get_authenticated_user),  # ✅ Required
) -> ReadUser:
    """
    Verify OTP sent to new email and complete the email change.

    - Requires authentication (same session from step 1)
    - Requires CSRF token
    - Validates email_change_token (anti-replay)
    - Validates OTP
    - Updates email in database
    - Returns updated user profile
    """
    return await verify_new_email(
        data=data,
        db=db,
        redis=redis,
        current_user=current_user,
)


