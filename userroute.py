redis: RedisDep = Depends(),
    mailer: MailDep = Depends(),





# CSRF PROTECTION
# =============================================================================

async def generate_csrf_token(
    user_id: int,
    redis: Redis,
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
    redis: Redis,
    user_id: int,
    csrf_token: str,
    #redis: RedisDep,
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
    redis: Redis,
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
