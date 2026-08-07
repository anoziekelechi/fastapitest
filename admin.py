# logics.py
async def get_users(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> list[User]:
    result = await db.execute(
        select(User)
        .order_by(User.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())





# router.py
@router.get("/", response_model=list[UserRead])
async def list_users(...):
    return await get_users(db, skip=skip, limit=limit)
