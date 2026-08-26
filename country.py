

        

async def get_country_by_name(
    db: AsyncSession,
    name: str,
) -> Country | None:
    """Fetch country by name (case-insensitive)."""
    result = await db.execute(
        select(Country).where(
            func.lower(Country.name) == name.lower()
        )
    )
    return result.scalars().first()

async def get_country_by_slug(
    db:AsyncSession,
    slug:str,
)->Country:
    result=await db.execute(
        select(Country).where(Country.slug==slug.lower().strip())
    )
    country=result.scalars().first()
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country {slug} not found"
        )
    return country
        


# =============================================================================
# ADMIN ACTIONS (Create, Update, Delete)
# =============================================================================

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
    # only admin
    # if not current_user.is_admin:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Action not allowed"
    #     )
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
        
    # check email support unique
    if data.email_support is not None:
        existing_email = (
            await db.execute(
                select(Country).where(
                    Country.email_support == data.email_support
                )
            )
        ).scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Support email '{data.email_support}' is already "
                       f"used by '{existing_email.name}'"
            )

    # ✅ Check whatsapp uniqueness
    if data.whatsapp is not None:
        existing_whatsapp = (
            await db.execute(
                select(Country).where(
                    Country.whatsapp == data.whatsapp
                )
            )
        ).scalars().first()
        if existing_whatsapp:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"WhatsApp number '{data.whatsapp}' is already "
                       f"used by '{existing_whatsapp.name}'"
            )
    
    country = Country(
        name=data.name,
        currency_code=data.currency_code,
        email_support=data.email_support,
        whatsapp=data.whatsapp,
        slug=generate_slug(data.name),          # ← set directly
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



async def read_single_country(
    db: AsyncSession,
    slug:str,
) -> CountryRead:
    # only admin soon
    """
    Get a single country by slug.

    Public - no authentication required.

    Args:
        country_id: Country ID to fetch
        db: Database session

    Returns:
        CountryRead: Country data

    Raises:
        HTTPException: 404 if not found
    """
    country = await get_country_by_slug(db, slug)
    
    
    return CountryRead.model_validate(country)


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
        await db.execute(
            select(func.count()).select_from(Country)
            )
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
    )





async def delete_country(
    db: AsyncSession,
    slug: str,
    #current_user: ReadUser,
) -> dict:
    """
    Delete a country by slug.

    Admin only.

    DB behavior:
        - Users: country_id set to NULL (ON DELETE SET NULL)
        - Offices: deleted automatically (ON DELETE CASCADE)
    """
    country = await get_country_by_slug(db, slug)

    country_name = country.name
    country_id = country.id

    await db.delete(country)
    await db.commit()

    # logger.info(
    #     f"Country '{country_name}' (id={country_id}) "
    #     f"deleted by admin {current_user.id}"
    # )

    return {"message": f"Country '{country_name}' deleted successfully"}
