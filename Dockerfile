
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
        )
    # wrong permission
    if user.permission != required_perm:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied - requires '{required_perm}' permission",
        )
    # Country scope check
    if target_country_id is not None:
        if user.country_id is None:
            raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied no country with such permission",
        )
        if user.country_id != target_country_id:
            raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You dont have permission on this country resource,",
            )



async def get_current_user(
    request: Request,
    db: AsyncSession,
) -> ReadUser:
    """
    Get current authenticated user from cookie.
    
    Loads permission ONCE here so all downstream
    has_permission() calls are pure in-memory - no extra DB queries.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_access_token(token)

    user = await get_user_by_id(db, payload.sub)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
        )

    if not user.verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified. Please verify your email.",
        )

    # ✅ Load permission ONCE - only 1 extra query for non-admins with a group
    # Admins skip this entirely (no query needed)
    permission: str | None = None
    if not user.is_admin and user.group_id is not None:
        from api.users.models import Group
        group = await db.get(Group, user.group_id)
        if group:
            permission = group.permission

    # ✅ Build ReadUser and inject permission
    read_user = ReadUser.model_validate(user)
    read_user.permission = permission
    return read_user


async def get_authenticated_user(
    request: Request,
    db: DBDep,
) -> ReadUser:
    """
    Dependency: Get current authenticated user.
    
    Usage:
        @router.get("/profile")
        async def profile(user: ReadUser = Depends(get_authenticated_user)):
            ...
    """
    return await get_current_user(request=request, db=db)





            def require_admin():
    """Dependency: require admin user."""
    async def dependency(
        request: Request,
        db: DBDep,
    ) -> ReadUser:
        user = await get_current_user(request, db)
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access Denied,Contact Admin"
            )
        return user
    return dependency
