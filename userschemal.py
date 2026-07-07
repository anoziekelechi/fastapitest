#api/uswes/schema.py 

async def reset_password(
    data: ResetPassword,
    db: AsyncSession,
    redis: Redis,
    current_user: ReadUser | None = None,
) -> dict:

# Invalidate ALL existing refresh tokens for this user
    # Forces re-login on all devices (security best practice after password reset)
    existing_tokens = await redis.smembers(f"user_refresh:{user_id}")
    if existing_tokens:
        for token in existing_tokens:
            try:
                # Decode each token to get its jti for blacklisting
                import jwt as pyjwt
                payload = pyjwt.decode(
                    token,
                    settings.public_key,
                    algorithms=[settings.algorithm],
                    options={"verify_exp": False},  # May already be expired
                )
                jti = payload.get("jti")
                if jti:
                    await redis.set(
                        f"blacklist:refresh:{jti}",
                        "1",
                        ex=int(
                            timedelta(
                                days=settings.refresh_token_expire_days
                            ).total_seconds()
                        )
                    )
            except Exception:
                continue  # Skip malformed tokens
        
        # Delete the entire active tokens set
        await redis.delete(f"user_refresh:{user_id}")
    
    logger.info(
        f"Password reset successful for user_id={user_id}. "
        f"All refresh tokens invalidated."
    )
    
    return {
        "message": "Password reset successful. "
                   "Please login with your new password."
                }


 


    
"Set[Unknown]" is not awaitable
  "Set[Unknown]" is incompatible with protocol "Awaitable[_T_co@Awaitable]"
    "__await__" is not presentPylancereportGeneralTypeIssues
(parameter) redis: Redis
redis: Redis client
