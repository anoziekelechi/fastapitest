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
        logger.exception(
            "Failed to fetch country name=%s",
            name,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load country. Please try again.",
        ) from None
