#chartgpt



# ==============================================================
# Update currency code
# ==============================================================

if (
    data.currency_code is not None
    and data.currency_code != country.currency_code
):
    existing_code = (
        await db.execute(
            select(Country).where(
                Country.currency_code == data.currency_code,
                Country.id != country.id,
            )
        )
    ).scalars().first()

    if existing_code is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Currency code '{data.currency_code}' is already "
                f"assigned to '{existing_code.name}'"
            ),
        )

    country.currency_code = data.currency_code
    updated_fields.append("currency_code")
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Adjust these imports to match your project
# from app.models.country import Country
# from app.schemas.country import CountryRead, CountryUpdate
# from app.schemas.user import ReadUser
# from app.core.permissions import Permissions, has_permission
# from app.utils.slug import generate_slug


async def update_country(
    slug: str,
    data: CountryUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> CountryRead:
    """
    Update an existing country identified by its slug.

    Only fields supplied in the request are considered for update.
    Unchanged values are ignored.

    Uniqueness is checked before updating:
        - name
        - currency_code
        - whatsapp
        - email_support

    The database should also have UNIQUE constraints for fields
    that must remain globally unique.
    """

    # ==============================================================
    # 1. Permission check
    # ==============================================================

    await has_permission(
        current_user,
        Permissions.MANAGE_COUNTRIES,
    )

    # ==============================================================
    # 2. Find country by slug
    # ==============================================================

    country = await get_country_by_slug(db, slug)

    if country is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with slug '{slug}' not found",
        )

    # ==============================================================
    # 3. Reject completely empty update payload
    # ==============================================================

    if all(
        value is None
        for value in (
            data.name,
            data.currency_code,
            data.whatsapp,
            data.email_support,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # Keep track of fields that actually changed.
    updated_fields: list[str] = []

    # ==============================================================
    # 4. Update country name + regenerate slug
    # ==============================================================

    if data.name is not None and data.name != country.name:

        # Check whether another country already uses this name.
        existing_country = await get_country_by_name(
            db,
            data.name,
        )

        if (
            existing_country is not None
            and existing_country.id != country.id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists",
            )

        old_name = country.name
        old_slug = country.slug

        country.name = data.name

        # Your current slug generator accepts only name.
        country.slug = generate_slug(data.name)

        updated_fields.extend(
            [
                "name",
                "slug",
            ]
        )

        logger.info(
            f"Country name changed: "
            f"'{old_name}' -> '{country.name}'. "
            f"Slug changed: "
            f"'{old_slug}' -> '{country.slug}'."
        )

    # ==============================================================
    # 5. Update currency code
    # ==============================================================

    if (
        data.currency_code is not None
        and data.currency_code != country.currency_code
    ):

        # Case-insensitive uniqueness check.
        #
        # Example:
        # Existing: USD
        # New:      usd
        #
        # These should be treated as the same currency code.
        existing_code = (
            await db.execute(
                select(Country).where(
                    func.upper(Country.currency_code)
                    == data.currency_code.upper(),
                    Country.id != country.id,
                )
            )
        ).scalars().first()

        if existing_code is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Currency code '{data.currency_code}' is already "
                    f"assigned to '{existing_code.name}'"
                ),
            )

        country.currency_code = data.currency_code
        updated_fields.append("currency_code")

    # ==============================================================
    # 6. Update WhatsApp
    # ==============================================================

    if (
        data.whatsapp is not None
        and data.whatsapp != country.whatsapp
    ):

        # Exclude the current country directly in SQL.
        existing_whatsapp = (
            await db.execute(
                select(Country).where(
                    Country.whatsapp == data.whatsapp,
                    Country.id != country.id,
                )
            )
        ).scalars().first()

        if existing_whatsapp is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"WhatsApp number '{data.whatsapp}' is already "
                    f"assigned to '{existing_whatsapp.name}'"
                ),
            )

        country.whatsapp = data.whatsapp
        updated_fields.append("whatsapp")

    # ==============================================================
    # 7. Update support email
    # ==============================================================

    if (
        data.email_support is not None
        and data.email_support != country.email_support
    ):

        # Exclude the current country directly in SQL.
        existing_email = (
            await db.execute(
                select(Country).where(
                    Country.email_support == data.email_support,
                    Country.id != country.id,
                )
            )
        ).scalars().first()

        if existing_email is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Support email '{data.email_support}' is already "
                    f"assigned to '{existing_email.name}'"
                ),
            )

        country.email_support = data.email_support
        updated_fields.append("email_support")

    # ==============================================================
    # 8. Make sure something actually changed
    # ==============================================================

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ==============================================================
    # 9. Save changes
    # ==============================================================

    db.add(country)

    await db.commit()

    # Refresh so the response contains the latest database state.
    await db.refresh(country)

    # ==============================================================
    # 10. Log update
    # ==============================================================

    logger.info(
        f"Country '{country.name}' "
        f"(slug='{country.slug}') "
        f"updated by user id={current_user.id}. "
        f"Fields: {', '.join(updated_fields)}"
    )

    # ==============================================================
    # 11. Return response
    # ==============================================================

    return CountryRead.model_validate(country)

#claude
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
