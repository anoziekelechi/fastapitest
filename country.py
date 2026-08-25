

async def delete_country(
    db: AsyncSession,
    slug: str,
    current_user: ReadUser,
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

    logger.info(
        f"Country '{country_name}' (id={country_id}) "
        f"deleted by admin {current_user.id}"
    )

    return {"message": f"Country '{country_name}' deleted successfully"}



@router.delete(
    "/{slug}",
    status_code=status.HTTP_200_OK,
    summary="Delete country by slug (admin only)",
)
async def delete_country_route(
    slug: str,
    db: AsyncSession = Depends(get_session),
    current_user: ReadUser = Depends(require_permission("can_manage_countries")),
    # or your admin dependency
) -> dict:
    return await delete_country(
        db=db,
        slug=slug,
        current_user=current_user,
    )





async def update_country(
    slug: str,
    data: CountryUpdate,
    db: AsyncSession,
    # current_user: ReadUser,
) -> CountryRead:
    """Update an existing country (identified by slug)."""

    # await has_permission(
    #     user=current_user,
    #     required_perm=Permissions.MANAGE_COUNTRIES,
    # )

    country = await get_country_by_slug(db, slug)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with slug '{slug}' not found",
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

    # Name (+ regenerate slug)
    if data.name is not None and data.name != country.name:
        existing = await get_country_by_name(db, data.name)
        if existing and existing.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists",
            )

        country.name = data.name
        country.slug = generate_slug(data.name)
        updated_fields.extend(["name", "slug"])

    # Currency code
    if data.currency_code is not None and data.currency_code != country.currency_code:
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
                detail=(
                    f"Currency code '{data.currency_code}' is already "
                    f"assigned to '{existing_code.name}'"
                ),
            )

        country.currency_code = data.currency_code
        updated_fields.append("currency_code")

    # WhatsApp (unique)
    if data.whatsapp is not None and data.whatsapp != country.whatsapp:
        existing_whatsapp = (
            await db.execute(
                select(Country).where(Country.whatsapp == data.whatsapp)
            )
        ).scalars().first()

        if existing_whatsapp and existing_whatsapp.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"WhatsApp number '{data.whatsapp}' is already "
                    f"assigned to '{existing_whatsapp.name}'"
                ),
            )

        country.whatsapp = data.whatsapp
        updated_fields.append("whatsapp")

    # Support email (unique)
    if data.email_support is not None and data.email_support != country.email_support:
        existing_email = (
            await db.execute(
                select(Country).where(Country.email_support == data.email_support)
            )
        ).scalars().first()

        if existing_email and existing_email.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Support email '{data.email_support}' is already "
                    f"assigned to '{existing_email.name}'"
                ),
            )

        country.email_support = data.email_support
        updated_fields.append("email_support")

    # Nothing actually changed
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes detected – all supplied values are identical to the current ones",
        )

    db.add(country)
    await db.commit()
    await db.refresh(country)

    # logger.info(
    #     f"Country '{country.name}' (slug={country.slug}) updated by id={current_user.id}. "
    #     f"Fields: {', '.join(updated_fields)}"
    # )

    return CountryRead.model_validate(country)


