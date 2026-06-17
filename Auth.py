
"""
Security utilities.

Token Strategy:
- Access token:  HttpOnly cookie (JS cannot read)
- Refresh token: HttpOnly cookie (JS cannot read)
- CSRF token:    Regular cookie (JS CAN read - needed to send in header)

Why CSRF token?
- HttpOnly cookies are sent automatically by browser
- Attacker can trick browser into sending cookies (CSRF attack)
- CSRF token in header proves the request came from YOUR frontend
- Because HttpOnly cookies are unreadable by JS, attacker
  cannot get the CSRF token to include in their forged request
"""
import json
import secrets
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import jwt
from passlib.context import CryptContext
from fastapi import HTTPException, Request, status, Response, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.core.settings import get_settings
from api.core.redis import RedisDep
from api.users.schemas import (
    TokenPayload,
    RefreshTokenPayload,
    TokenResponse,
    CSRFData,
)


# =============================================================================
# SETUP
# =============================================================================

settings = get_settings()

# ✅ Removed: oauth2_scheme - not needed with HttpOnly cookies!

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# =============================================================================
# COOKIE CONFIGURATION
# =============================================================================

# Centralize cookie settings - easy to change in one place
COOKIE_CONFIG = {
    "httponly": True,   # ← JS cannot read
    "secure": True,     # ← HTTPS only (set False for local dev)
    "samesite": "lax",  # ← Protects against CSRF
}

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"
CSRF_TOKEN_COOKIE = "csrf_token"   # ← NOT httponly - JS needs to read this


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> None:
    """
    Set all auth cookies on response.
    Called after login or token refresh.
    
    Args:
        response: FastAPI response object
        access_token: JWT access token
        refresh_token: JWT refresh token
        csrf_token: CSRF token
    """
    # Access token - HttpOnly, JS cannot read
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,                  # ✅ XSS protection
        secure=settings.is_production(), # ✅ HTTPS in production
        samesite="lax",                 # ✅ CSRF protection
    )
    
    # Refresh token - HttpOnly, JS cannot read
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE,
        value=refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        httponly=True,                  # ✅ XSS protection
        secure=settings.is_production(),
        samesite="lax",
    )
    
    # CSRF token - NOT HttpOnly, JS needs to read and send in header
    response.set_cookie(
        key=CSRF_TOKEN_COOKIE,
        value=csrf_token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=False,                 # ✅ JS can read this
        secure=settings.is_production(),
        samesite="lax",
    )


def clear_auth_cookies(response: Response) -> None:
    """
    Clear all auth cookies (logout).
    
    Args:
        response: FastAPI response object
    """
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    response.delete_cookie(REFRESH_TOKEN_COOKIE)
    response.delete_cookie(CSRF_TOKEN_COOKIE)


# =============================================================================
# PASSWORD UTILITIES
# =============================================================================

def hash_password(password: str) -> str:
    """Hash a plain text password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


# =============================================================================
# TOKEN CREATION
# =============================================================================

def create_access_token(user_id: int) -> str:
    """
    Create signed JWT access token.
    
    Args:
        user_id: User's database ID
        
    Returns:
        str: Signed JWT
    """
    now = datetime.now(timezone.utc)
    payload = TokenPayload(
        sub=user_id,
        iat=int(now.timestamp()),
        exp=int((now + timedelta(
            minutes=settings.access_token_expire_minutes
        )).timestamp()),
        jti=secrets.token_urlsafe(32),
        token_type="access",
    )
    
    return jwt.encode(
        payload.model_dump(),
        settings.private_key.get_secret_value(),
        algorithm=settings.algorithm,
    )


async def create_refresh_token(
    user_id: int,
    redis: RedisDep,
) -> str:
    """
    Create signed JWT refresh token and store in Redis.
    
    Args:
        user_id: User's database ID
        redis: Redis client
        
    Returns:
        str: Signed JWT
    """
    now = datetime.now(timezone.utc)
    jti = str(uuid4())
    
    payload = RefreshTokenPayload(
        sub=user_id,
        iat=int(now.timestamp()),
        exp=int((now + timedelta(
            days=settings.refresh_token_expire_days
        )).timestamp()),
        jti=jti,
        token_type="refresh",
    )
    
    token = jwt.encode(
        payload.model_dump(),
        settings.private_key.get_secret_value(),
        algorithm=settings.algorithm,
    )
    
    # Store in Redis for rotation/replay protection
    redis_key = f"user_refresh:{user_id}"
    await redis.sadd(redis_key, token)
    await redis.expire(
        redis_key,
        int(timedelta(days=settings.refresh_token_expire_days).total_seconds())
    )
    
    return token


async def create_token_response(
    user_id: int,
    redis: RedisDep,
) -> TokenResponse:
    """
    Create all tokens for login response.
    
    Args:
        user_id: User's database ID
        redis: Redis client
        
    Returns:
        TokenResponse: All tokens
    """
    access_token = create_access_token(user_id)
    refresh_token = await create_refresh_token(user_id, redis)
    csrf_token = await generate_csrf_token(user_id, redis)
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )


# =============================================================================
# TOKEN EXTRACTION FROM COOKIES
# =============================================================================

async def get_access_token_from_cookie(request: Request) -> str:
    """
    Extract access token from HttpOnly cookie.
    
    ✅ No Authorization header needed
    ✅ Browser sends cookie automatically
    
    Args:
        request: FastAPI request
        
    Returns:
        str: JWT access token
        
    Raises:
        HTTPException: If cookie missing
    """
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return token


async def get_refresh_token_from_cookie(request: Request) -> str:
    """
    Extract refresh token from HttpOnly cookie.
    
    Args:
        request: FastAPI request
        
    Returns:
        str: JWT refresh token
        
    Raises:
        HTTPException: If cookie missing
    """
    token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing",
        )
    return token


# =============================================================================
# TOKEN VALIDATION
# =============================================================================

def decode_access_token(token: str) -> TokenPayload:
    """
    Decode and validate access token.
    
    Args:
        token: JWT string
        
    Returns:
        TokenPayload: Validated payload
        
    Raises:
        HTTPException: On invalid/expired token
    """
    try:
        payload = jwt.decode(
            token,
            settings.public_key,
            algorithms=[settings.algorithm],
            options={"require": ["exp", "iat", "sub", "jti", "token_type"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
        )
    
    return TokenPayload(**payload)


async def validate_refresh_token(
    refresh_token: str,
    redis: RedisDep,
) -> int:
    """
    Validate refresh token with replay protection.
    
    Steps:
    1. Quick pre-check (no crypto)
    2. Blacklist check
    3. Full crypto verification
    4. Replay protection
    
    Args:
        refresh_token: JWT refresh token
        redis: Redis client
        
    Returns:
        int: User ID
    """
    # Step 1: Quick pre-check
    try:
        unverified = jwt.decode(
            refresh_token,
            options={"verify_signature": False}
        )
        jti = unverified.get("jti")
        
        if not jti or unverified.get("token_type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token"
        )
    
    # Step 2: Blacklist check
    if await redis.get(f"blacklist:refresh:{jti}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked"
        )
    
    # Step 3: Full crypto verification
    try:
        payload = jwt.decode(
            refresh_token,
            settings.public_key,
            algorithms=[settings.algorithm],
            options={"require": ["exp", "iat", "sub", "jti", "token_type"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    user_id = int(payload["sub"])
    
    # Step 4: Replay protection
    is_valid = await redis.sismember(f"user_refresh:{user_id}", refresh_token)
    if not is_valid:
        await redis.set(
            f"blacklist:refresh:{jti}",
            "1",
            ex=int(timedelta(days=settings.refresh_token_expire_days).total_seconds())
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no longer valid - please login again"
        )
    
    return user_id


# =============================================================================
# TOKEN REVOCATION
# =============================================================================

async def revoke_refresh_token(
    refresh_token: str,
    redis: RedisDep,
) -> None:
    """Revoke a single refresh token."""
    try:
        payload = jwt.decode(
            refresh_token,
            settings.public_key,
            algorithms=[settings.algorithm],
        )
        user_id = int(payload["sub"])
        jti = payload["jti"]
        
        await redis.srem(f"user_refresh:{user_id}", refresh_token)
        await redis.set(
            f"blacklist:refresh:{jti}",
            "1",
            ex=int(timedelta(days=settings.refresh_token_expire_days).total_seconds())
        )
    except Exception:
        pass  # Already invalid


async def revoke_all_user_tokens(
    user_id: int,
    redis: RedisDep,
) -> None:
    """Revoke ALL refresh tokens for user (logout everywhere)."""
    redis_key = f"user_refresh:{user_id}"
    tokens = await redis.smembers(redis_key)
    
    for token in tokens:
        try:
            payload = jwt.decode(
                token,
                options={"verify_signature": False}
            )
            jti = payload.get("jti")
            if jti:
                await redis.set(
                    f"blacklist:refresh:{jti}",
                    "1",
                    ex=int(timedelta(days=settings.refresh_token_expire_days).total_seconds())
                )
        except Exception:
            continue
    
    await redis.delete(redis_key)


# =============================================================================
# CSRF PROTECTION
# =============================================================================

async def generate_csrf_token(
    user_id: int,
    redis: RedisDep,
) -> str:
    """Generate and store CSRF token in Redis."""
    csrf_token = secrets.token_urlsafe(32)
    expiry_seconds = settings.access_token_expire_minutes * 60
    
    csrf_data = CSRFData(
        user_id=user_id,
        expires_at=int(
            (datetime.now(timezone.utc) + timedelta(
                seconds=expiry_seconds
            )).timestamp()
        )
    )
    
    await redis.set(
        f"csrf:{user_id}:{csrf_token}",
        json.dumps(csrf_data.model_dump()),
        ex=expiry_seconds,
    )
    
    return csrf_token


async def verify_csrf_token(
    user_id: int,
    csrf_token: str,
    redis: RedisDep,
) -> bool:
    """Verify CSRF token against Redis."""
    key = f"csrf:{user_id}:{csrf_token}"
    raw = await redis.get(key)
    
    if not raw:
        return False
    
    try:
        csrf_info = CSRFData(**json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        await redis.delete(key)
        return False
    
    if csrf_info.user_id != user_id:
        return False
    
    if csrf_info.expires_at < int(datetime.now(timezone.utc).timestamp()):
        await redis.delete(key)
        return False
    
    return True


async def csrf_protection(
    request: Request,
    redis: RedisDep,
    user_id: int,
) -> None:
    """
    Validate CSRF token.
    
    Flow:
    1. Browser sends cookie automatically (HttpOnly)
    2. Frontend JS reads csrf_token cookie (NOT httponly)
    3. Frontend sends csrf_token in X-CSRF-Token header
    4. We compare header value against Redis
    """
    header_token = request.headers.get("X-CSRF-Token")
    
    if not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing from headers"
        )
    
    if not await verify_csrf_token(user_id, header_token, redis):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired CSRF token"
        )


# =============================================================================
# DEPENDENCIES
# =============================================================================

async def get_current_user_id(
    token: str = Depends(get_access_token_from_cookie),
) -> int:
    """
    Dependency: Get current authenticated user ID.
    
    Usage:
        @router.get("/profile")
        async def profile(user_id: int = Depends(get_current_user_id)):
            ...
    """
    payload = decode_access_token(token)
    return payload.sub


async def get_user_by_id(db: AsyncSession, user_id: int):
    """Fetch user by ID."""
    from api.users.models import User
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalars().first()


async def get_user_by_email(db: AsyncSession, email: str):
    """Fetch user by email."""
    from api.users.models import User
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first()
