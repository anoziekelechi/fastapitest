

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
    db: AsyncSession = Depends(get_db),
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

#route
File "/app/api/main.py", line 10, in <module>
backend-1  |     from api.users.routes import router as user_router
backend-1  |   File "/app/api/users/routes.py", line 93, in <module>
backend-1  |     @router.post(
backend-1  |      ^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 1063, in decorator
backend-1  |     self.add_api_route(
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 1002, in add_api_route
backend-1  |     route = route_class(
backend-1  |             ^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 621, in __init__
backend-1  |     self.dependant = get_dependant(
backend-1  |                      ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/dependencies/utils.py", line 298, in get_dependant
backend-1  |     sub_dependant = get_dependant(
backend-1  |                     ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/dependencies/utils.py", line 276, in get_dependant
backend-1  |     param_details = analyze_param(
backend-1  |                     ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/dependencies/utils.py", line 501, in analyze_param
backend-1  |     field = create_model_field(
backend-1  |             ^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/utils.py", line 95, in create_model_field
backend-1  |     raise fastapi.exceptions.FastAPIError(
backend-1  | fastapi.exceptions.FastAPIError: Invalid args for response field! Hint: check that <class 'sqlalchemy.ext.asyncio.session.AsyncSession'> is a valid Pydantic field type. If you are using a return type annotation that is not a valid Pydantic field (e.g. Union[Response, dict, None]) you can disable generating the response model from the type annotation with the path operation decorator parameter response_model=None. Read more: https://fastapi.tiangolo.com/tutorial/response-model/







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
