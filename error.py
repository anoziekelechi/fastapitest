
                                                                                                                                                                                                                                                                                                                                                                                                                        

async def read_single_country(
    country_id: int,#slugslug
    db: AsyncSession,
) -> CountryRead:
    """
    Get a single country by ID.

    Public - no authentication required.

    Args:
        country_id: Country ID to fetch
        db: Database session

    Returns:
        CountryRead: Country data

    Raises:
        HTTPException: 404 if not found
    """
    country = await get_country_by_id(db, country_id)#await get_country_by_slug(db, slug)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {country_id} not found"
        )
    
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
    
    




async def update_country(
    country_id: int,
    data: CountryUpdate,
    db: AsyncSession,
    #current_user: ReadUser,
) -> CountryRead:
    """Update an existing country."""

    # await has_permission(
    #     user=current_user,
    #     required_perm=Permissions.MANAGE_COUNTRIES,
    # )

    country = await get_country_by_id(db, country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {country_id} not found",
        )

    # Reject completely empty payloads
    if all(
        v is None
        for v in [
            data.name,
            data.currency_code,
            data.whatsapp,
            data.email_support,
        ]
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    updated_fields: list[str] = []

    # Name
    if data.name is not None and data.name != country.name:
        existing = await get_country_by_name(db, data.name)
        if existing and existing.id != country_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists",
            )
        country.name = data.name
        updated_fields.append("name")

    # Currency code
    if data.currency_code is not None and data.currency_code != country.currency_code:
        existing_code = (
            await db.execute(
                select(Country).where(
                    func.upper(Country.currency_code) == data.currency_code.upper()
                )
            )
        ).scalars().first()
        if existing_code and existing_code.id != country_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Currency code '{data.currency_code}' is already "
                    f"assigned to '{existing_code.name}'"
                ),
            )
        country.currency_code = data.currency_code
        updated_fields.append("currency_code")
        
        
    # WhatsApp (now unique)
    if data.whatsapp is not None and data.whatsapp != country.whatsapp:
        existing_whatsapp = (
        await db.execute(
            select(Country).where(Country.whatsapp == data.whatsapp)
        )
    ).scalars().first()
        if existing_whatsapp and existing_whatsapp.id != country_id:
            raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"WhatsApp number '{data.whatsapp}' is already assigned to '{existing_whatsapp.name}'",
        )
        country.whatsapp = data.whatsapp
        updated_fields.append("whatsapp")

# Support email (now unique)
    if data.email_support is not None and data.email_support != country.email_support:
        existing_email = (
        await db.execute(
            select(Country).where(Country.email_support == data.email_support)
        )
    ).scalars().first()
        if existing_email and existing_email.id != country_id:
            raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Support email '{data.email_support}' is already assigned to '{existing_email.name}'",
        )
        country.email_support = data.email_support
        updated_fields.append("email_support")



    
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes detected – all supplied values are identical to the current ones",
        )

    db.add(country)
    await db.commit()
    await db.refresh(country)

    # logger.info(
    #     f"Country '{country.name}' updated by id={current_user.id}. "
    #     f"Fields: {', '.join(updated_fields)}"
    # )

    return CountryRead.model_validate(country)



async def delete_country(
    country_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a country.

    Admin only.

    Flow:
        1. Fetch country (404 if not found)
        2. Check no users are assigned to this country
        3. Check no offices are assigned to this country
        4. Delete

    Args:
        country_id: ID of country to delete
        db: Database session
        current_user: Authenticated admin user

    Returns:
        dict: Success message

    Raises:
        HTTPException: 404 if not found
        HTTPException: 400 if country has assigned users or offices
    """
    country = await get_country_by_id(db, country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {country_id} not found"
        )
    
    # Prevent deletion if users are assigned
    if country.users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete '{country.name}' - it has "
                   f"{len(country.users)} assigned user(s). "
                   f"Reassign or remove users first."
        )
    
    # Prevent deletion if offices are assigned
    if country.offices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete '{country.name}' - it has "
                   f"{len(country.offices)} assigned office(s). "
                   f"Remove offices first."
        )
    
    country_name = country.name
    await db.delete(country)
    await db.commit()
    
    logger.info(
        f"Country '{country_name}' (id={country_id}) "
        f"deleted by admin {current_user.id}"
    )
    
    return {"message": f"Country '{country_name}' deleted successfully"}









