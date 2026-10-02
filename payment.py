 # =============================================================================
# HELPERS
# =============================================================================
async def get_firm_by_slug(
    db:AsyncSession,
    slug:str,
)-> Firm:
    
    normalized_slug = slug.strip().lower()
    result = await db.execute(
        select(Firm).where(Firm.slug== normalized_slug)
    )
    firm=result.scalars().first()
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country {slug} not found"
        )
    return firm



async def get_firm_by_name(
    db: AsyncSession,
    name: str,
) -> Firm | None:
    
    normalize_name = normalize_firm_name(name)
    result = await db.execute(select(Firm).where(Firm.name == normalize_name))
    return result.scalars().first()

# =============================================================================
# CREATE
# =============================================================================

async def create_firm(
    data: FirmCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a firm profile. A user may own multiple firms.
    """

    user_id = current_user.id

    # ------------------------------------------------------------------
    # Name uniqueness (global)
    # ------------------------------------------------------------------
    normalize_name=normalize_firm_name(data.name)
    existing_name = await get_firm_by_name(db, normalize_name)
    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"firm '{data.name}' already exists",
        )


    # ------------------------------------------------------------------
    # Registration number uniqueness
    # ------------------------------------------------------------------
    if data.registration_number:
        try:
            existing_reg = (
                await db.execute(
                    select(Firm).where(
                        Firm.registration_number == data.registration_number
                    )
                )
            ).scalars().first()
        except Exception:
            logger.exception(
                "Failed to check existing registration_number=%s",
                data.registration_number,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create firm. Please try again.",
            )

        if existing_reg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Registration number '{data.registration_number}' "
                    f"is already registered"
                ),
            )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    firm = Firm(
        user_id=user_id,
        name=data.name,
        slug=generate_slug(normalize_name),
        registration_number=data.registration_number,
        address=data.address,
        phone_number=data.phone_number,
        deals_on=data.deals_on,
    )

    try:
        db.add(firm)
        await db.commit()
        await db.refresh(firm)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create firm for user_id=%s", user_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create firm profile. Please try again.",
        )

    logger.info(
        "Firm '%s' (slug=%s) created by user_id=%s",
        firm.name,
        firm.slug,
        user_id,
    )

    return {
        "message": f"Firm '{firm.name}' created successfully",
        "firm": FirmRead.model_validate(firm),
    }


# =============================================================================
# READ
# =============================================================================

async def read_my_firms(
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmListRead:
    """Get all firms owned by the authenticated user."""
    try:
        result = await db.execute(
            select(Firm)
            .where(Firm.user_id == current_user.id)
            .order_by(Firm.name)
        )
        firms = result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s", current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load your firms. Please try again.",
        )

    return FirmListRead(
        total=len(firms),
        firms=[FirmRead.model_validate(f) for f in firms],
    )


async def read_all_firms(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> FirmListRead:
    """List all firms. only admin can read all firm in db."""
    try:
        total: int = (
            await db.execute(select(func.count()).select_from(Firm))
        ).scalar() or 0

        result = await db.execute(
            select(Firm).order_by(Firm.name).offset(skip).limit(limit)
        )
        firms = result.scalars().all()
    except Exception:
        logger.exception("Failed to list firms")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firms. Please try again.",
        )

    return FirmListRead(
        total=total,
        firms=[FirmRead.model_validate(f) for f in firms],
    )


async def read_single_firm(
    slug: str,
    db: AsyncSession,
    #current_user: ReadUser,
) -> FirmRead:
    """Get a single firm by slug. only owner can access his own firm"""
    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )
    return FirmRead.model_validate(firm)


# =============================================================================
# UPDATE
# =============================================================================

async def update_firm(
    slug: str,
    data: FirmUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update a firm. Owner or admin only.

    Returns { message, firm }.
    """

    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    # ------------------------------------------------------------------
    # Ownership check
    # ------------------------------------------------------------------
    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    # ------------------------------------------------------------------
    # Reject empty update
    # ------------------------------------------------------------------
    if all(
        v is None
        for v in (
            data.name,
            data.registration_number,
            data.address,
            data.phone_number,
            data.deals_on,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ------------------------------------------------------------------
    # Track changed fields
    # ------------------------------------------------------------------
    updated_fields: list[str] = []

    # --- name (+ slug) ---
    if data.name is not None and data.name != firm.name:
        try:
            existing = (
                await db.execute(
                    select(Firm).where(
                        func.lower(Firm.name) == data.name.lower(),
                        Firm.id != firm.id,
                    )
                )
            ).scalars().first()
        except Exception:
            logger.exception(
                "Failed to check duplicate firm name=%s", data.name
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update firm. Please try again.",
            )

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Firm name '{data.name}' is already taken",
            )

        old_slug = firm.slug
        firm.name = data.name
        firm.slug = generate_slug(data.name)
        updated_fields.append("name")
        logger.info(
            "Firm slug updated: '%s' -> '%s'", old_slug, firm.slug
        )

    # --- registration_number ---
    if (
        data.registration_number is not None
        and data.registration_number != firm.registration_number
    ):
        try:
            existing_reg = (
                await db.execute(
                    select(Firm).where(
                        Firm.registration_number == data.registration_number,
                        Firm.id != firm.id,
                    )
                )
            ).scalars().first()
        except Exception:
            logger.exception(
                "Failed to check duplicate registration_number=%s",
                data.registration_number,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update firm. Please try again.",
            )

        if existing_reg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Registration number '{data.registration_number}' "
                    f"is already registered"
                ),
            )

        firm.registration_number = data.registration_number
        updated_fields.append("registration_number")

    # --- address ---
    if data.address is not None and data.address != firm.address:
        firm.address = data.address
        updated_fields.append("address")

    # --- phone_number ---
    if (
        data.phone_number is not None
        and data.phone_number != firm.phone_number
    ):
        firm.phone_number = data.phone_number
        updated_fields.append("phone_number")

    # --- deals_on ---
    if data.deals_on is not None and data.deals_on != firm.deals_on:
        firm.deals_on = data.deals_on
        updated_fields.append("deals_on")

    # ------------------------------------------------------------------
    # Reject no-op update
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
    try:
        db.add(firm)
        await db.commit()
        await db.refresh(firm)
    except Exception:
        await db.rollback()
        logger.exception("Failed to update firm slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update firm profile. Please try again.",
        )

    logger.info(
        "Firm '%s' updated by user_id=%s (fields=%s)",
        firm.name,
        current_user.id,
        ",".join(updated_fields),
    )

    return {
        "message": f"Firm '{firm.name}' updated successfully",
        "firm": FirmRead.model_validate(firm),
    }


# =============================================================================
# DELETE
# =============================================================================

async def delete_firm(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Delete a firm. Owner or admin only."""

    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    firm_name = firm.name

    try:
        await db.delete(firm)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete firm slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete firm. Please try again.",
        )

    logger.info(
        "Firm '%s' deleted by user_id=%s", firm_name, current_user.id
    )

    return {"message": f"Firm '{firm_name}' deleted successfully"}

