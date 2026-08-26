
async def update_country(
    slug: str,
    data: CountryUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> CountryRead:
    """Update country with uniqueness checks."""

    await has_permission(current_user, Permissions.MANAGE_COUNTRIES)

    country = await get_country_by_slug(db, slug)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country '{slug}' not found"
        )

    if all(v is None for v in [
        data.name, data.currency_code,
        data.whatsapp, data.email_support,
    ]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided"
        )

    updated_fields = []

    # Update name
    if data.name is not None:
        if data.name == country.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New name is the same as current"
            )
        existing = await get_country_by_name(db, data.name)
        if existing and existing.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists"
            )
        country.name = data.name
        old_slug = country.slug
        country.slug = generate_slug(data.name, country.id)  # type: ignore[arg-type]
        updated_fields.append("name")
        logger.info(f"Slug updated: '{old_slug}' → '{country.slug}'")

    # Update currency code
    if data.currency_code is not None:
        if data.currency_code == country.currency_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New currency code is the same as current"
            )
        existing_code = (
            await db.execute(
                select(Country).where(
                    func.upper(Country.currency_code) == data.currency_code.upper()
                )
            )
        ).scalars().first()
        if existing_code and existing_code.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Currency code '{data.currency_code}' already "
                       f"assigned to '{existing_code.name}'"
            )
        country.currency_code = data.currency_code
        updated_fields.append("currency_code")

    # ✅ Update whatsapp with uniqueness check
    if data.whatsapp is not None:
        if data.whatsapp == country.whatsapp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New WhatsApp number is the same as current"
            )
        existing_whatsapp = (
            await db.execute(
                select(Country).where(
                    Country.whatsapp == data.whatsapp,
                    Country.id != country.id,   # ← exclude current country
                )
            )
        ).scalars().first()
        if existing_whatsapp:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"WhatsApp '{data.whatsapp}' already used "
                       f"by '{existing_whatsapp.name}'"
            )
        country.whatsapp = data.whatsapp
        updated_fields.append("whatsapp")

    # ✅ Update email_support with uniqueness check
    if data.email_support is not None:
        if data.email_support == country.email_support:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New support email is the same as current"
            )
        existing_email = (
            await db.execute(
                select(Country).where(
                    Country.email_support == data.email_support,
                    Country.id != country.id,   # ← exclude current country
                )
            )
        ).scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Support email '{data.email_support}' already "
                       f"used by '{existing_email.name}'"
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
