
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from fastapi import HTTPException, status, UploadFile, Depends
from sqlmodel import select
from api.core.slug import generate_slug
from api.home.schemas import ReadHome
from api.models import Home
from api.core.file_storage import (
    delete_from_r2, handle_file_update, get_public_url,
    validate_image_file_securely,
    get_public_url,
    LOGO_FOLDER,
    BANNER_FOLDER,
    LOGO_MAX_SIZE,
    BANNER_MAX_SIZE,
    delete_from_r2
    )


import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func

from api.models.home import Country
from api.home.schemas import (
    CountryCreate,
    CountryUpdate,
    CountryRead,
    CountryListRead,
)
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)

# db: AsyncSession = Depends(get_db),

LOGO_MAX_SIZE = 5 * 1024 * 1024   # 5 MiB
HERO_MAX_SIZE = 8 * 1024 * 1024   # 8 MiB



async def setup_home_logic(
    db: AsyncSession,
    sitename: str | None = None,
    intro: str | None = None,
    logo_key: UploadFile | None = None,
    banner_key: UploadFile | None = None,
) -> ReadHome:
    """
    Create or partially update the MAIN home configuration.

    Behavior:

        First setup:
            - sitename is required
            - intro is optional
            - logo is optional
            - banner is optional

        Existing setup:
            - all fields are optional
            - only supplied fields are updated
    """

    # =========================================================================
    # Get current MAIN configuration
    # =========================================================================

    result = await db.execute(
        select(Home).where(
            Home.config_type == "MAIN"
        )
    )

    current = result.scalars().first()

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

        cleaned_intro = (
            intro.strip()
            if intro is not None
            else None
        )

        # -------------------------------------------------------------
        # Upload logo
        # -------------------------------------------------------------

        new_logo_key = await handle_file_update(
            file=logo_key,
            current_key=None,
            prefix=LOGO_FOLDER,
            max_size=LOGO_MAX_SIZE,
        )

        # -------------------------------------------------------------
        # Upload banner
        # -------------------------------------------------------------

        new_banner_key = await handle_file_update(
            file=banner_key,
            current_key=None,
            prefix=BANNER_FOLDER,
            max_size=BANNER_MAX_SIZE,
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

            # Database failed after files were uploaded.
            delete_from_r2(new_logo_key)
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

        # -------------------------------------------------------------
        # Site name
        # -------------------------------------------------------------

        if sitename is not None:

            cleaned_sitename = sitename.strip()

            if not cleaned_sitename:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Site name cannot be empty",
                )

            current.sitename = cleaned_sitename

        # -------------------------------------------------------------
        # Intro
        # -------------------------------------------------------------

        if intro is not None:
            current.intro = intro.strip()

        # -------------------------------------------------------------
        # Logo
        # -------------------------------------------------------------

        if logo_key is not None:

            new_logo_key = await handle_file_update(
                file=logo_key,
                current_key=old_logo_key,
                prefix=LOGO_FOLDER,
                max_size=LOGO_MAX_SIZE,
            )

            current.logo_key = new_logo_key

        # -------------------------------------------------------------
        # Banner
        # -------------------------------------------------------------

        if banner_key is not None:

            new_banner_key = await handle_file_update(
                file=banner_key,
                current_key=old_banner_key,
                prefix=BANNER_FOLDER,
                max_size=BANNER_MAX_SIZE,
            )

            current.banner_key = new_banner_key

        # -------------------------------------------------------------
        # Check whether anything was actually supplied
        # -------------------------------------------------------------

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

        await db.commit()
        await db.refresh(current)

    except HTTPException:
        await db.rollback()

        # If validation failed after uploading a replacement,
        # remove the new orphaned file.
        if new_logo_key and new_logo_key != old_logo_key:
            delete_from_r2(new_logo_key)

        if new_banner_key and new_banner_key != old_banner_key:
            delete_from_r2(new_banner_key)

        raise

    except Exception as exc:

        await db.rollback()

        # Remove newly uploaded files if DB operation failed.
        if new_logo_key and new_logo_key != old_logo_key:
            delete_from_r2(new_logo_key)

        if new_banner_key and new_banner_key != old_banner_key:
            delete_from_r2(new_banner_key)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update home configuration",
        ) from exc

    # =========================================================================
    # Delete old files ONLY after successful DB commit
    # =========================================================================

    if (
        new_logo_key
        and old_logo_key
        and new_logo_key != old_logo_key
    ):
        delete_from_r2(old_logo_key)

    if (
        new_banner_key
        and old_banner_key
        and new_banner_key != old_banner_key
    ):
        delete_from_r2(old_banner_key)

    # =========================================================================
    # Response
    # =========================================================================

    return ReadHome(
        id=current.id,
        sitename=current.sitename,
        intro=current.intro,
        logo_key=get_public_url(current.logo_key),
        banner_key=get_public_url(current.banner_key),
    )


# =============================================================================
# PUBLIC HOME SETTINGS
# =============================================================================

async def get_home_settings_logic(
    db: AsyncSession,
) -> ReadHome:
    """
    Get the MAIN home configuration.

    This function is public because it is used by the site's
    public navigation/home context.
    """

    result = await db.execute(
        select(Home).where(
            Home.config_type == "MAIN"
        )
    )

    home = result.scalars().first()

    # Default values when setup has not been completed.
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



  
    
# Country
# =============================================================================
# HELPERS
# =============================================================================



async def get_country_by_name(
    db: AsyncSession,
    name: str,
) -> Country | None:
    """Fetch country by name (case-insensitive)."""
    normalize_name = name.strip().lower()
    result = await db.execute(
        select(Country).where(
            func.lower(Country.name) == normalize_name
        )
    )
    return result.scalars().first()


async def get_country_by_slug(
    db:AsyncSession,
    slug:str,
)-> Country:
    
    normalized_slug = slug.strip().lower()
    result = await db.execute(
        select(Country).where(Country.slug== normalized_slug)
    )
    country=result.scalars().first()
    if country is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country {slug} not found"
        )
    return country
        


# =============================================================================
# ADMIN ACTIONS (Create, Update, Delete)
# ✅ Admin check done in route via require_admin
# =============================================================================

async def create_country(
    data: CountryCreate,
    db: AsyncSession,
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
    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Country '{data.name}' already exists"
        )
    #generate slug
    country_slug=generate_slug(data.name)
    # Check currency code uniqueness
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
        if existing_whatsapp is not None:
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
        slug=country_slug,          # ← set directly
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
    
    """
    Get a single country by slug.


    Args:
       
        db: Database session

    Returns:
        CountryRead: Country data

    Raises:
        HTTPException: 404 if not found
    """
    # if not current_user.is_admin:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Action not allowed"
    #     )
    
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
    ).scalar_one() #or 0
    
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
    

    await db.delete(country)
    await db.commit()

    # logger.info(
    #     f"Country '{country_name}'"
    #     f"deleted by admin {current_user.id}"
    # )

    return {"message": f"Country '{country_name}' deleted successfully"}




async def update_country(
    slug: str,
    data: CountryUpdate,
    db: AsyncSession,
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
    # 2. Find country
    # ==============================================================

    country = await get_country_by_slug(db, slug)

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
    # 11. Return updated country
    # ==============================================================

    return CountryRead.model_validate(country)

