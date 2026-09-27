# api/home/routes.py (excerpt — routes that changed)

from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile, File, Form, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import DBDep
from api.home.schemas import (
    CountryCreate,
    CountryListRead,
    CountryRead,
    CountryUpdate,
    CreateCountryResponse,
    MessageResponse,
    ReadHome,
    UpdateCountryResponse,
)
from api.home.logics import (
    create_country,
    delete_country,
    get_home_settings_logic,
    read_all_countries,
    read_single_country,
    setup_home_logic,
    update_country,
)
from api.users.deps import require_admin

router = APIRouter(prefix="/home", tags=["Home"])


# =============================================================================
# COUNTRY
# =============================================================================

@router.post(
    "/add_country",
    response_model=CreateCountryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new country (admin only)",
)
async def add_country(
    data: CountryCreate,
    db: DBDep,
    _: None = Depends(require_admin),
):
    return await create_country(data=data, db=db)


@router.get(
    "/countries",
    response_model=CountryListRead,
    status_code=status.HTTP_200_OK,
    summary="List countries",
)
async def list_countries(
    db: DBDep,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_countries(db=db, skip=skip, limit=limit)


@router.get(
    "/{slug}",
    response_model=CountryRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single country",
)
async def get_country(slug: str, db: DBDep):
    return await read_single_country(db=db, slug=slug)


@router.patch(
    "/{slug}",
    response_model=UpdateCountryResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a country (admin only)",
)
async def patch_country(
    slug: str,
    data: CountryUpdate,
    db: DBDep,
    _: None = Depends(require_admin),
):
    return await update_country(slug=slug, data=data, db=db)


@router.delete(
    "/{slug}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a country (admin only)",
)
async def remove_country(
    slug: str,
    db: DBDep,
    _: None = Depends(require_admin),
):
    return await delete_country(db=db, slug=slug)


# =============================================================================
# HOME SETTINGS
# =============================================================================

@router.post(
    "/setup",
    response_model=ReadHome,
    status_code=status.HTTP_200_OK,
)
async def setup_home(
    db: DBDep,
    sitename: str | None = Form(None),
    intro: str | None = Form(None),
    logo_key: UploadFile | None = File(None),
    banner_key: UploadFile | None = File(None),
    _: None = Depends(require_admin),
):
    return await setup_home_logic(
        db=db,
        sitename=sitename,
        intro=intro,
        logo_key=logo_key,
        banner_key=banner_key,
    )


@router.get(
    "/settings",
    response_model=ReadHome,
    status_code=status.HTTP_200_OK,
)
async def get_home_settings(db: DBDep):
    return await get_home_settings_logic(db=db)



# api/home/logics.py

import logging

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from api.core.file_storage import (
    BANNER_FOLDER,
    BANNER_MAX_SIZE,
    LOGO_FOLDER,
    LOGO_MAX_SIZE,
    delete_from_r2,
    get_public_url,
    handle_file_update,
)
from api.core.slug import generate_slug
from api.home.schemas import (
    CountryCreate,
    CountryListRead,
    CountryRead,
    CountryUpdate,
    ReadHome,
)
from api.models import Home
from api.models.home import Country

logger = logging.getLogger(__name__)

LOGO_MAX_SIZE = 5 * 1024 * 1024
HERO_MAX_SIZE = 8 * 1024 * 1024


# =============================================================================
# HOME SETTINGS
# =============================================================================

async def setup_home_logic(
    db: AsyncSession,
    sitename: str | None = None,
    intro: str | None = None,
    logo_key: UploadFile | None = None,
    banner_key: UploadFile | None = None,
) -> ReadHome:
    """
    Create or partially update the MAIN home configuration.
    """

    # ------------------------------------------------------------------
    # Load existing config
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(Home).where(Home.config_type == "MAIN")
        )
        current = result.scalars().first()
    except Exception:
        logger.exception("Failed to load MAIN home config")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load home configuration",
        )

    # =========================================================================
    # FIRST-TIME SETUP
    # =========================================================================
    if current is None:
        if sitename is None or not sitename.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Site name is required for initial home setup",
            )

        cleaned_sitename = sitename.strip()
        cleaned_intro = intro.strip() if intro is not None else None

        # Uploads happen BEFORE the DB insert so we can clean up on failure
        new_logo_key = await handle_file_update(
            file=logo_key, current_key=None,
            prefix=LOGO_FOLDER, max_size=LOGO_MAX_SIZE,
        )
        new_banner_key = await handle_file_update(
            file=banner_key, current_key=None,
            prefix=BANNER_FOLDER, max_size=BANNER_MAX_SIZE,
        )

        try:
            record = Home(
                config_type="MAIN",
                sitename=cleaned_sitename,
                intro=cleaned_intro,
                logo_key=new_logo_key,
                banner_key=new_banner_key,
            )
            db.add(record)
            await db.commit()
            await db.refresh(record)
        except Exception as exc:
            await db.rollback()
            logger.exception("Failed to create home configuration")

            # Best-effort cleanup of files uploaded before commit
            if new_logo_key:
                delete_from_r2(new_logo_key)
            if new_banner_key:
                delete_from_r2(new_banner_key)

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create home configuration",
            ) from exc

        return ReadHome(
            id=record.id,
            sitename=record.sitename,
            intro=record.intro,
            logo_key=get_public_url(record.logo_key),
            banner_key=get_public_url(record.banner_key),
        )

    # =========================================================================
    # PARTIAL UPDATE
    # =========================================================================
    old_logo_key = current.logo_key
    old_banner_key = current.banner_key

    new_logo_key: str | None = None
    new_banner_key: str | None = None

    try:
        if sitename is not None:
            cleaned = sitename.strip()
            if not cleaned:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Site name cannot be empty",
                )
            current.sitename = cleaned

        if intro is not None:
            current.intro = intro.strip()

        if logo_key is not None:
            new_logo_key = await handle_file_update(
                file=logo_key, current_key=old_logo_key,
                prefix=LOGO_FOLDER, max_size=LOGO_MAX_SIZE,
            )
            current.logo_key = new_logo_key

        if banner_key is not None:
            new_banner_key = await handle_file_update(
                file=banner_key, current_key=old_banner_key,
                prefix=BANNER_FOLDER, max_size=BANNER_MAX_SIZE,
            )
            current.banner_key = new_banner_key

        if (
            sitename is None
            and intro is None
            and logo_key is None
            and banner_key is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one field must be provided",
            )

        db.add(current)
        await db.commit()
        await db.refresh(current)

    except HTTPException:
        await db.rollback()
        # Validation failed AFTER uploads → remove orphans
        if new_logo_key and new_logo_key != old_logo_key:
            delete_from_r2(new_logo_key)
        if new_banner_key and new_banner_key != old_banner_key:
            delete_from_r2(new_banner_key)
        raise

    except Exception as exc:
        await db.rollback()
        logger.exception("Failed to update home configuration")

        if new_logo_key and new_logo_key != old_logo_key:
            delete_from_r2(new_logo_key)
        if new_banner_key and new_banner_key != old_banner_key:
            delete_from_r2(new_banner_key)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update home configuration",
        ) from exc

    # Delete old files ONLY after successful commit
    if new_logo_key and old_logo_key and new_logo_key != old_logo_key:
        delete_from_r2(old_logo_key)
    if new_banner_key and old_banner_key and new_banner_key != old_banner_key:
        delete_from_r2(old_banner_key)

    return ReadHome(
        id=current.id,
        sitename=current.sitename,
        intro=current.intro,
        logo_key=get_public_url(current.logo_key),
        banner_key=get_public_url(current.banner_key),
    )


async def get_home_settings_logic(db: AsyncSession) -> ReadHome:
    """Public — returns MAIN config or a safe default."""
    try:
        result = await db.execute(
            select(Home).where(Home.config_type == "MAIN")
        )
        home = result.scalars().first()
    except Exception:
        logger.exception("Failed to load home settings")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load home settings",
        )

    if home is None:
        return ReadHome(
            id=0,
            sitename="Ecommerce",
            intro="",
            logo_key=None,
            banner_key=None,
        )

    return ReadHome(
        id=home.id,
        sitename=home.sitename,
        intro=home.intro,
        logo_key=get_public_url(home.logo_key),
        banner_key=get_public_url(home.banner_key),
    )


# =============================================================================
# COUNTRY HELPERS
# =============================================================================

async def get_country_by_name(db: AsyncSession, name: str) -> Country | None:
    normalized = name.strip().lower()
    result = await db.execute(
        select(Country).where(func.lower(Country.name) == normalized)
    )
    return result.scalars().first()


async def get_country_by_slug(db: AsyncSession, slug: str) -> Country:
    normalized = slug.strip().lower()
    result = await db.execute(
        select(Country).where(Country.slug == normalized)
    )
    country = result.scalars().first()
    if country is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country '{slug}' not found",
        )
    return country


# =============================================================================
# CREATE COUNTRY
# =============================================================================

async def create_country(
    data: CountryCreate,
    db: AsyncSession,
) -> dict:
    """Create a new country. Admin only."""

    # ------------------------------------------------------------------
    # Name uniqueness
    # ------------------------------------------------------------------
    existing_name = await get_country_by_name(db, data.name)
    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Country '{data.name}' already exists",
        )

    country_slug = generate_slug(data.name)

    # ------------------------------------------------------------------
    # Currency code uniqueness
    # ------------------------------------------------------------------
    existing_code = (
        await db.execute(
            select(Country).where(
                Country.currency_code == data.currency_code
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

    # ------------------------------------------------------------------
    # Support email uniqueness
    # ------------------------------------------------------------------
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
                detail=(
                    f"Support email '{data.email_support}' is already "
                    f"used by '{existing_email.name}'"
                ),
            )

    # ------------------------------------------------------------------
    # WhatsApp uniqueness
    # ------------------------------------------------------------------
    if data.whatsapp is not None:
        existing_whatsapp = (
            await db.execute(
                select(Country).where(Country.whatsapp == data.whatsapp)
            )
        ).scalars().first()
        if existing_whatsapp is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"WhatsApp number '{data.whatsapp}' is already "
                    f"used by '{existing_whatsapp.name}'"
                ),
            )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    country = Country(
        name=data.name,
        currency_code=data.currency_code,
        email_support=data.email_support,
        whatsapp=data.whatsapp,
        slug=country_slug,
    )

    db.add(country)
    try:
        await db.commit()
        await db.refresh(country)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create country name=%s", data.name
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create country. Please try again.",
        )

    return {
        "message": f"Country '{country.name}' created successfully",
        "country": CountryRead.model_validate(country),
    }


# =============================================================================
# READ
# =============================================================================

async def read_single_country(db: AsyncSession, slug: str) -> CountryRead:
    country = await get_country_by_slug(db, slug)
    return CountryRead.model_validate(country)


async def read_all_countries(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> CountryListRead:
    try:
        total: int = (
            await db.execute(select(func.count()).select_from(Country))
        ).scalar_one()

        result = await db.execute(
            select(Country)
            .order_by(Country.name)
            .offset(skip)
            .limit(limit)
        )
        countries = result.scalars().all()
    except Exception:
        logger.exception("Failed to list countries")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load countries. Please try again.",
        )

    return CountryListRead(
        total=total,
        countries=[CountryRead.model_validate(c) for c in countries],
    )


# =============================================================================
# DELETE COUNTRY
# =============================================================================

async def delete_country(db: AsyncSession, slug: str) -> dict:
    """
    Delete a country by slug. Admin only.

    DB behavior:
        - Users: country_id set to NULL (ON DELETE SET NULL)
        - Offices: deleted automatically (ON DELETE CASCADE)
    """
    country = await get_country_by_slug(db, slug)
    country_name = country.name

    try:
        await db.delete(country)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete country slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete country. Please try again.",
        )

    logger.info("Country '%s' deleted", country_name)

    return {"message": f"Country '{country_name}' deleted successfully"}


# =============================================================================
# UPDATE COUNTRY
# =============================================================================

async def update_country(
    slug: str,
    data: CountryUpdate,
    db: AsyncSession,
) -> dict:
    """
    Update an existing country identified by slug.

    Only supplied fields are considered for update.
    Returns { message, country }.
    """

    country = await get_country_by_slug(db, slug)

    # ------------------------------------------------------------------
    # Reject empty update
    # ------------------------------------------------------------------
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

    updated_fields: list[str] = []

    # ------------------------------------------------------------------
    # name + slug
    # ------------------------------------------------------------------
    if data.name is not None and data.name != country.name:
        existing_country = await get_country_by_name(db, data.name)
        if existing_country is not None and existing_country.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists",
            )

        old_name = country.name
        old_slug = country.slug
        country.name = data.name
        country.slug = generate_slug(data.name)
        updated_fields.extend(["name", "slug"])

        logger.info(
            "Country name changed: '%s' -> '%s'. Slug: '%s' -> '%s'",
            old_name, country.name, old_slug, country.slug,
        )

    # ------------------------------------------------------------------
    # currency_code
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # whatsapp
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # email_support
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # No actual change
    # ------------------------------------------------------------------
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    db.add(country)
    try:
        await db.commit()
        await db.refresh(country)
    except Exception:
        await db.rollback()
        logger.exception("Failed to update country slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update country. Please try again.",
        )

    return {
        "message": f"Country '{country.name}' updated successfully",
        "country": CountryRead.model_validate(country),
    }
