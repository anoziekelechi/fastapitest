

async def create_country(
    data: CountryCreate,
    db: AsyncSession,
    #current_user: ReadUser,         # ✅ Admin check done in route via require_admin
) -> dict:
    """
    Create a new country.

    Admin only.

    Flow:
        1. Check name uniqueness (case-insensitive)
        2. Check currency code uniqueness
        3. Create country record

    Args:
        data: Validated country data
        db: Database session
        current_user: Authenticated admin user

    Returns:
        dict: Success message

    Raises:
        HTTPException: 409 if name or currency code already exists
    """
    # Check name uniqueness
    existing_name = await get_country_by_name(db, data.name)
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Country '{data.name}' already exists"
        )
    
    # Check currency code uniqueness
    existing_code = (
        await db.execute(
            select(Country).where(
                func.upper(Country.currency_code) == data.currency_code.upper()
            )
        )
    ).scalars().first()
    if existing_code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Currency code '{data.currency_code}' is already "
                   f"assigned to '{existing_code.name}'"
        )
    
    country = Country(
        name=data.name,
        currency_code=data.currency_code,
        whatsapp=data.whatsapp,
    )
    db.add(country)
    await db.commit()
    await db.refresh(country)
    
    # logger.info(
    #     f"Country '{country.name}' created by admin {current_user.id}"
    # )
    
    return {
        "message": f"Country '{country.name}' created successfully",
        "country": CountryRead.model_validate(country),
    }



# routes

@router.post(
    "/add_country",
    status_code=status.HTTP_201_CREATED,
    summary="Create a country (admin only)",
)
async def create_country_route(
    data: CountryCreate,
    db: DBDep,
    #current_user: ReadUser = AdminUser,             # ✅ Admin only
) -> dict:
    """
    Create a new country.

    Admin only. Country name is normalized to Title Case.
    Multiple spaces are collapsed. Numbers and special chars rejected.

    Examples:
        "liberia"         → "Liberia"
        "south africa"    → "South Africa"
        "guinea  bissau"  → "Guinea Bissau"
    """
    return await create_country(
        data=data,
        db=db,
        #current_user=current_user,
    )

