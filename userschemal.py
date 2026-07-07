#api/uswes/schema.py 


async def create_token_response(
    user_id: int,
    redis: Redis,
) -> TokenData:                    # ← Returns TokenData, not TokenResponse
    access_token = create_access_token(user_id)
    refresh_token = await create_refresh_token(user_id, redis)
    csrf_token = await generate_csrf_token(user_id, redis)
    
    return TokenData(
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )
 


    
