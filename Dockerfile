
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


