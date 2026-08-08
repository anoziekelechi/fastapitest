async def read_all_countries(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> CountryListRead:
    """
    List all countries with pagination.

    Public - no authentication required.

    Args:
        db: Database session
        skip: Records to skip (pagination offset)
        limit: Maximum records to return

    Returns:
        CountryListRead: Total count + paginated list
    """
    # Get total count
    total: int = (
        await db.execute(select(func.count(Country.id)))
    ).scalar() or 0
    
    # Get paginated results
    result = await db.execute(
        select(Country)
        .order_by(Country.name)
        .offset(skip)
        .limit(limit)
    )
    countries = result.scalars().all()
    
    return CountryListRead(
        total=total,

 countries=[CountryRead.model_validate(c) for c in countries],




    Argument of type "int | None" cannot be assigned to parameter "expression" of type "_ColumnExpressionArgument[Any] | _StarOrOne | None" in function "__init__"
  Type "int | None" is not assignable to type "_ColumnExpressionArgument[Any] | _StarOrOne | None"
    Type "int" is not assignable to type "_ColumnExpressionArgument[Any] | _StarOrOne | None"
     
       
    )
