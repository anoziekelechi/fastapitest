async def get_country_by_name(
    db: AsyncSession,
    name: str,
) -> Country | None:
    
    try:
        normalize_name=validate_country_name(name)
        result = await db.execute(
            select(Country).where(Country.name == normalize_name)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm name=%s", name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )

vs
async def get_country_by_name(
    db: AsyncSession,
    name: str,
) -> Country | None:
   
    try:
       
        result = await db.execute(
            select(Country).where(Country.name == name)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm name=%s", name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )
