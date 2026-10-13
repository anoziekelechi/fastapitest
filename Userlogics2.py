async def handle_disabled_account(
    data: ContactAdminMessage,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Handle contact form from disabled users.
    """

    user = await get_user_by_email(db, data.email)

    # Generic response – don't reveal account status
    if not user or not user.disabled:
        return {
            "message": "If your account exists, "
                       "your message has been sent to our support team."
        }

    user_id = get_user_id(user)
    
    # Rate limit – max 3 per hour
    rate_key = f"contact_admin_rate:{user_id}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, 
            int(timedelta(minutes=settings.contact_rate_window).total_seconds()),
            ) #3600
    if count > settings.contact_rate_limit:
        ttl = await redis.ttl(rate_key)
        default_window = int(timedelta(minutes=settings.contact_rate_window).total_seconds())
        retry_after = (
            ttl if isinstance(ttl,int) and ttl > 0 else default_window
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again in 1 hour.",
            headers={"Retry-After":str(retry_after)},
        )

    # Extract Primitive from ORM objects
    full_names = user.full_names
    # Resolve support email (country → fallback)
    support_email = settings.mail_username

    if user.country_id:
        country = await db.get(Country, user.country_id)
        if country and country.email_support:
            support_email = country.email_support

    background_tasks.add_task(
        send_support_message,
        user_full_names=full_names,
        support_email=support_email,
        user_email=data.email,
        message=data.message,
        mailer=mailer,
    )

    logger.info(
        "Disabled user %s (id=%s) contacted support → %s",
        data.email,
        user_id,
        support_email,
    )

    return {
        "message": "Your message has been sent to our support team. "
                   "We will review your account and get back to you."
    }




#=================================================================
async def get_user_profile(
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Load full user + relationships, then build profile dict
    with role-based fields.
    """
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.country),  # type: ignore[arg-type]
            selectinload(User.group),    # type: ignore[arg-type]
        )
        .where(User.id == current_user.id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    profile: dict = {
        "id": user.id,
        "email": user.email,
        "surname": user.surname,
        "othernames": user.othernames,
        #"country": user.country.name if user.country else None,
        "verified": user.verified,
        "disabled": user.disabled,
        "date_verified": user.date_verified,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "full_names": user.full_names,
        "avatar": user.avatar
    }

    # Group info (only if user belongs to a group)
    if user.group:
        profile["name"] = user.group.name
        profile["permission"] = user.group.permission

    # Admin-only field
    if user.is_admin:
        profile["is_admin"] = True

    return profile





async def has_permission(
    user: ReadUser,
    required_perm: str,
    target_country_id: int | None = None,
) -> None:
    """
    Pure in-memory permission check.
    No DB queries - permission already loaded in get_current_user.
    
    Hierarchy:
        1. Admin → all permissions ✅
        2. User with matching permission → allowed ✅
        3. Everyone else → 403 ❌
    
    Args:
        user: ReadUser with .permission already set
        required_perm: Required permission string
    
    Raises:
        HTTPException: 403 if permission denied
    """
    # Admins bypass all permission checks
    if user.is_admin:
        return

    # No permission
    if user.permission is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access deny,you dont have permission",
            headers={"X-Error-Code":"no_permission"},
        )
    # wrong permission
    if user.permission != required_perm:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied - requires '{required_perm}' permission",
            headers={"X-Error-Code":"wrong_permission"},
        )
    # Country scope check
    if target_country_id is not None:
        if user.country_id is None:
            raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied no country with such permission",
            headers={"X-Error-Code":"no_country_scope"},
        )
        if user.country_id != target_country_id:
            raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You dont have permission on this country resource,",
            headers={"X-Error-Code":"wrong_country_scope"},
            )


