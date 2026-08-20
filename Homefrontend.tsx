async def update_country(
    country_id: int,
    data: CountryUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> CountryRead:
    """Update an existing country."""
    
    # ✅ Permission check
    await has_permission(
        user=current_user,
        required_perm=Permissions.MANAGE_COUNTRIES,
    )
    
    country = await get_country_by_id(db, country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {country_id} not found"
        )
    
    # ✅ Cleaner all-None check
    if all(v is None for v in [
        data.name,
        data.currency_code,
        data.whatsapp,
        data.email_support,
    ]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update"
        )
    
    updated_fields = []
    
    # Update name
    if data.name is not None:
        if data.name == country.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New country name is the same as current name"
            )
        existing = await get_country_by_name(db, data.name)
        if existing and existing.id != country_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists"
            )
        country.name = data.name
        updated_fields.append("name")
    
    # Update currency code
    if data.currency_code is not None:
        if data.currency_code == country.currency_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New currency code is the same as current code"
            )
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
                detail=f"Currency code '{data.currency_code}' is already "
                       f"assigned to '{existing_code.name}'"
            )
        country.currency_code = data.currency_code
        updated_fields.append("currency_code")
    
    # Update whatsapp
    if data.whatsapp is not None:
        country.whatsapp = data.whatsapp
        updated_fields.append("whatsapp")
    
    # ✅ Update email_support (was completely missing!)
    if data.email_support is not None:
        if data.email_support == country.email_support:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New support email is the same as current"
            )
        country.email_support = data.email_support
        updated_fields.append("email_support")
    
    db.add(country)
    await db.commit()
    await db.refresh(country)
    
    logger.info(
        f"Country '{country.name}' updated by id={current_user.id}. "
        f"Fields: {', '.join(updated_fields)}"
    )
    
    return CountryRead.model_validate(country)



#old
async def update_country(
    country_id: int,
    data: CountryUpdate,
    db: AsyncSession,
    #current_user: ReadUser,
) -> CountryRead:
    """
    Update an existing country.

    Admin only.

    Flow:
        1. Fetch country (404 if not found)
        2. Validate at least one field provided
        3. Check name uniqueness if name is being changed
        4. Check currency uniqueness if currency is being changed
        5. Apply updates (only provided fields)

    Args:
        country_id: ID of country to update
        data: Partial update data
        db: Database session
        current_user: Authenticated admin user

    Returns:
        CountryRead: Updated country

    Raises:
        HTTPException: 404 if not found
        HTTPException: 400 if no fields provided
        HTTPException: 409 if name or currency already taken
    """
    country = await get_country_by_id(db, country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {country_id} not found"
        )
    
    # At least one field must be provided
    if data.name is None and data.currency_code is None and data.whatsapp is None and data.email_support is None :
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update"
        )
    
    updated_fields = []
    
    # Update name if provided and different
    if data.name is not None:
        if data.name == country.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New country name is the same as current name"
            )
        # Check uniqueness
        existing = await get_country_by_name(db, data.name)
        if existing and existing.id != country_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists"
            )
        country.name = data.name
        updated_fields.append("name")
    
    # Update currency code if provided and different
    if data.currency_code is not None:
        if data.currency_code == country.currency_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New currency code is the same as current code"
            )
        # Check uniqueness
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
                detail=f"Currency code '{data.currency_code}' is already "
                       f"assigned to '{existing_code.name}'"
            )
        country.currency_code = data.currency_code
        updated_fields.append("currency_code")
    
    # Update whatsapp if provided
    if data.whatsapp is not None:
        country.whatsapp = data.whatsapp
        updated_fields.append("whatsapp")
    
    db.add(country)
    await db.commit()
    await db.refresh(country)
    
    logger.info(
        f"Country '{country.name}' updated by admin."  # {current_user.id}
        f"Fields: {', '.join(updated_fields)}"
    )
    
    return CountryRead.model_validate(country)

