
@router.get("/profile", response_model= ReadUser)
async def get_profile(user:User = Depends(current_user), _: None=Depends(csrf_protection)):
    return ReadUser.model_validate(user)



@router.post("/register")
async def register(
    user_data: UserCreate,
    #background_tasks: BackgroundTasks = Depends(BackgroundTasks),
    db: DB,
    redis: RedisDep,
    mailer: Mailer,
    background_tasks: BackgroundTasks = Depends(BackgroundTasks),
):
    result = await add_user(
        user_data=user_data,
        db=db,
        background_tasks=background_tasks,
        redis=redis,
        mailer=mailer,
    )
    return result  # contains message + reg_token + email


# ── VERIFY REGISTRATION OTP
@router.post("/register/verify")
async def verify_reg_otp(
    data: VerifyOtp,
    db: DB,
    redis: RedisDep,
) ->dict:
    user = await verify_registration_otp(data, db, redis)
    return {"message": "Account verified successfully", "user": user}

# routes/auth.py — FINAL, THIN, SACRED, ETERNAL
from fastapi import APIRouter, Depends, Request, HTTPException, status
from deps import DB, RedisDep
from schemas.auth import LoginStep1, LoginStep2
from crud.auth import initiate_password_login, complete_password_login, logout
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/auth", tags=["auth"])

# ── STEP 1: Password → Send OTP
@router.post("/login")
async def login_step1(
    data: LoginStep1,
    
    db: DB,
    redis: RedisDep,
    mailer: Mailer,
    background_tasks: BackgroundTasks = Depends(),
):
    return await initiate_password_login(
        email=data.email,
        password=data.password,
        background_tasks=background_tasks,
        db=db,
        redis=redis,
        mailer=mailer,
    )

# ── STEP 2: OTP → Issue tokens + set cookies
@router.post("/login/verify")
async def login_step2(
    data: LoginStep2,
    db: DB,
    redis: RedisDep,
):
    tokens = await complete_password_login(data, db, redis)

    response = JSONResponse(content={"message": "Login successful"})
    response.set_cookie("access_token",  tokens.access_token,  httponly=True, secure=True, samesite="lax", max_age=900, path="/")
    response.set_cookie("refresh_token", tokens.refresh_token, httponly=True, secure=True, samesite="lax", max_age=30*24*60*60, path="/auth/refresh")
    response.set_cookie("csrf_token",    tokens.csrf_token,    httponly=False, secure=True, samesite="lax", max_age=30*24*60*60)
    
    return response

# ── REFRESH TOKEN
@router.post("/refresh")
async def refresh_token(
    request: Request,
    redis: RedisDep,
):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")

    # validate_refresh_token returns user_id + jti
    user_id, jti = await validate_refresh_token(refresh_token, redis)

    # Revoke old one
    await logout(user_id=user_id, jti=jti, refresh_token=refresh_token, redis=redis)

    # Issue new ones
    tokens = await create_token_response(user_id, redis)

    response = JSONResponse(content={"message": "Refreshed"})
    response.set_cookie("access_token",  tokens.access_token,  httponly=True, secure=True, samesite="lax", max_age=900, path="/")
    response.set_cookie("refresh_token", tokens.refresh_token, httponly=True, secure=True, samesite="lax", max_age=30*24*60*60, path="/auth/refresh")
    response.set_cookie("csrf_token",    tokens.csrf_token,    httponly=False, secure=True, samesite="lax", max_age=30*24*60*60)

    return response

# ── LOGOUT
@router.post("/logout")
async def logout_endpoint(
    request: Request,
    redis: RedisDep,
):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="No active session")

    try:
        payload = jwt.decode(refresh_token, PUBLIC_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
        jti = payload["jti"]
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

    await logout(user_id=user_id, jti=jti, refresh_token=refresh_token, redis=redis)

    response = JSONResponse(content={"message": "Logged out successfully"})
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/auth/refresh")
    response.delete_cookie("csrf_token")

    return response


@router.post("/refresh")
async def refresh_token_endpoint(request: Request):
    old_refresh_token = request.cookies.get("refresh_token")
    if not old_refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")

    user_id = await validate_refresh_token(old_refresh_token)
    await revoke_refresh_token(old_refresh_token)

    new_tokens = await create_token_response(user_id)

    # DO NOT send tokens in body — security risk + duplication
    response = JSONResponse(content={"message": "Tokens refreshed"})

    # Only set in secure HttpOnly cookies
    response.set_cookie(
        key="access_token",
        value=new_tokens.access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=20 * 60,
        path="/"
    )
    response.set_cookie(
        key="refresh_token",
        value=new_tokens.refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
        path="/"
    )
    response.set_cookie(
        key="csrf_token",
        value=new_tokens.csrf_token,
        httponly=False,   # JS needs to read
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
        path="/"
    )

    return response


@router.post("/logout")
async def logout(
    request: Request,
    _: None = Depends(require_csrf_protection),
    current_user = Depends(get_current_user)
):
    refresh_token = request.cookies.get("refresh_token")
    await logout_user(refresh_token)

    response = JSONResponse({"message": "Logged out"})
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    response.delete_cookie("csrf_token")
    return response




@router.post("/login")
async def login_step1(
    data: LoginStep1,
    background_tasks: BackgroundTasks,
    db = Depends(get_session),
    current_user = Depends(get_current_user_optional)
):
    if current_user:
        raise HTTPException(status_code=403, detail="Already logged in")

    result = await initiate_login(
        email=data.email,
        password=data.password,
        db=db,
        background_tasks=background_tasks
    )
    return result


@router.post("/login/verify")
async def login_step2(
    data: LoginStep2,
    db = Depends(get_session)
):
    tokens = await complete_login(
        email=data.email,
        otp=data.otp,
        login_token=data.login_token,
        db=db
    )

    response = JSONResponse({
        "message": "Login successful",
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "csrf_token": tokens["csrf_token"]
    })

    response.set_cookie("access_token", tokens["access_token"], httponly=True, secure=True, samesite="lax", max_age=20*60)
    response.set_cookie("refresh_token", tokens["refresh_token"], httponly=True, secure=True, samesite="lax", max_age=30*24*60*60)
    response.set_cookie("csrf_token", tokens["csrf_token"], httponly=False, secure=True, samesite="lax", max_age=30*24*60*60)

    return response

@router.post("/logout")
async def logout(
    request: Request,
    _: None = Depends(require_csrf_protection),
    current_user = Depends(get_current_user)
):
    refresh_token = request.cookies.get("refresh_token")
    await logout_user(refresh_token)

    response = JSONResponse({"message": "Logged out successfully"})
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/auth/refresh")
    response.delete_cookie("csrf_token", path="/")
    return response

##### latest routes

"""
User authentication routes.

Flow:
    Registration:
        POST /auth/register         → Send OTP
        POST /auth/register/verify  → Verify OTP → Account active

    Login (2FA):
        POST /auth/login            → Validate credentials → Send OTP
        POST /auth/login/verify     → Verify OTP → Set cookies

    Token Management:
        POST /auth/refresh          → Rotate tokens
        POST /auth/logout           → Revoke + clear cookies

    Profile:
        GET  /auth/profile          → Get current user
        PUT  /auth/profile/names    → Update names
        PUT  /auth/password         → Change password
        DELETE /auth/account        → Delete account
"""
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
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_session
from api.core.redis import RedisDep
from api.core.mail import MailDep
from api.core.security import (
    validate_refresh_token,
    revoke_refresh_token,
    create_token_response,
    set_auth_cookies,
    clear_auth_cookies,
    csrf_protection,
    ACCESS_TOKEN_COOKIE,
    REFRESH_TOKEN_COOKIE,
    CSRF_TOKEN_COOKIE,
)
from api.core.settings import get_settings
from api.users.logics import (
    register_user,
    verify_registration_otp,
    initiate_login,
    complete_login,
    logout_user,
    get_current_user,
    change_password,
    delete_account,
)
from api.users.schemas import (
    CreateUser,
    LoginRequest,
    VerifyOtpRequest,
    UpdateNames,
    UpdatePassword,
    VerifyPassword,
    ReadUser,
)


settings = get_settings()

router = APIRouter(prefix="/auth", tags=["Authentication"])


# =============================================================================
# DEPENDENCIES
# =============================================================================

async def get_db_session() -> AsyncSession:
    """Database session dependency."""
    async for session in get_session():
        yield session


async def get_authenticated_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> ReadUser:
    """
    Dependency: Get current authenticated user.
    
    Usage:
        @router.get("/profile")
        async def profile(user: ReadUser = Depends(get_authenticated_user)):
            ...
    """
    return await get_current_user(request=request, db=db)


async def require_csrf(
    request: Request,
    redis: RedisDep,
    current_user: ReadUser = Depends(get_authenticated_user),
) -> None:
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
    background_tasks: BackgroundTasks,   # ✅ No Depends() needed
    db: AsyncSession = Depends(get_db_session),
    redis: RedisDep = Depends(),
    mailer: MailDep = Depends(),
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
    )


@router.post(
    "/register/verify",
    status_code=status.HTTP_200_OK,
    response_model=ReadUser,
    summary="Verify registration OTP",
)
async def verify_registration(
    data: VerifyOtpRequest,
    db: AsyncSession = Depends(get_db_session),
    redis: RedisDep = Depends(),
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
    background_tasks: BackgroundTasks,   # ✅ No Depends() needed
    db: AsyncSession = Depends(get_db_session),
    redis: RedisDep = Depends(),
    mailer: MailDep = Depends(),
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
    )


@router.post(
    "/login/verify",
    status_code=status.HTTP_200_OK,
    summary="Login step 2 - verify OTP",
    response_description="Auth cookies set",
)
async def verify_login(
    data: VerifyOtpRequest,
    response: Response,                  # ✅ FastAPI injects this
    db: AsyncSession = Depends(get_db_session),
    redis: RedisDep = Depends(),
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
    redis: RedisDep = Depends(),
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
    redis: RedisDep = Depends(),
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
    db: AsyncSession = Depends(get_db_session),
    current_user: ReadUser = Depends(get_authenticated_user),
) -> ReadUser:
    """Update user's surname and/or othernames."""
    from api.users.logics import update_user_names
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
    db: AsyncSession = Depends(get_db_session),
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
    db: AsyncSession = Depends(get_db_session),
    redis: RedisDep = Depends(),
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
