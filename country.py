
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Adjust these imports to your project structure.
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

    Only supplied fields are considered for update.

    Uniqueness is checked for:
        - name
        - currency_code
        - whatsapp
        - email_support

    When the country name changes, its slug is regenerated using
    generate_slug(name).
    """

    # ==============================================================
    # 1. Permission check
    # ==============================================================

    await has_permission(
        current_user,
        Permissions.MANAGE_COUNTRIES,
    )

    # ==============================================================
    # 2. Find country
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
    # 4. Update country name + slug
    # ==============================================================

    if data.name is not None and data.name != country.name:

        # Check whether another country already has this name.
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

        # Your generate_slug() now accepts only the name.
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
        # currency_code is already normalized to uppercase
        # before this service logic.
        #
        # Therefore there is no need for func.upper() here.
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

    # ==============================================================
    # 6. Update WhatsApp
    # ==============================================================

    if (
        data.whatsapp is not None
        and data.whatsapp != country.whatsapp
    ):
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
    # 8. Nothing actually changed
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

    # Reload the object from the database.
    await db.refresh(country)

    # ==============================================================
    # 10. Log update
    # ==============================================================

    logger.info(
        f"Country '{country.name}' "
        f"(slug='{country.slug}') updated by "
        f"user id={current_user.id}. "
        f"Fields: {', '.join(updated_fields)}"
    )

    # ==============================================================
    # 11. Return updated country
    # ==============================================================

    return CountryRead.model_validate(country)
