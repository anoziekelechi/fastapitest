
async def validate_refresh_token(
    refresh_token: str,
    redis: Redis,
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
    r: Any = redis
    is_valid: bool = bool(await r.sismember(f"user_refresh:{user_id}", refresh_token))
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
    redis: Redis,
) -> None:
    r: Any = redis
    """Revoke a single refresh token."""
    try:
        payload = jwt.decode(
            refresh_token,
            settings.public_key,
            algorithms=[settings.algorithm],
        )
        user_id = int(payload["sub"])
        jti = payload["jti"]
        
        await r.srem(f"user_refresh:{user_id}", refresh_token)
        await r.set(
            f"blacklist:refresh:{jti}",
            "1",
            ex=int(timedelta(days=settings.refresh_token_expire_days).total_seconds())
        )
    except Exception:
        pass  # Already invalid

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
