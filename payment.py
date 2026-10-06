
async def create_firm(
    data: FirmCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Create a firm. Caller must be verified and active."""
    _guard_user_state(current_user)

    # ------------------------------------------------------------------
    # Name uniqueness
    # ------------------------------------------------------------------
    existing_name = await get_firm_by_name(db, data.name)
    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Firm '{data.name}' already exists",
        )

    # ------------------------------------------------------------------
    # Registration number uniqueness — only when provided
    # ------------------------------------------------------------------
    if data.registration_number is not None:
        existing_reg = await get_firm_by_registration_number(
            db, data.registration_number
        )
        if existing_reg is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Registration number "
                    f"'{data.registration_number}' is already "
                    f"registered to '{existing_reg.name}'"
                ),
            )

    normalized_name = normalize_firm_name(data.name)

    firm = Firm(
        user_id=current_user.id,
        name=normalized_name,
        slug=generate_slug(normalized_name),
        registration_number=data.registration_number,  # may be None
        deals_on=data.deals_on,
        disabled=False,
    )

    try:
        db.add(firm)
        await db.commit()
        await db.refresh(firm)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create firm for user_id=%s", current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create firm profile. Please try again.",
        )

    logger.info(
        "Firm '%s' (reg=%s) created by user_id=%s",
        firm.name,
        firm.registration_number or "—",
        current_user.id,
    )

    return {
        "message": f"Firm '{firm.name}' created successfully",
        "firm": FirmRead.model_validate(firm),
  }


# --- registration_number ---
# Only check/set when explicitly provided.
#
# Note: we do NOT allow clearing the registration number via
# update once it's set. If a firm has a registration number,
# it should stay — the number is a legal identity.
# To clear it, that's a support action.
if (
    data.registration_number is not None
    and data.registration_number != firm.registration_number
):
    existing_reg = await get_firm_by_registration_number(
        db, data.registration_number
    )
    if (
        existing_reg is not None
        and firm_id_of(existing_reg) != firm_id_of(firm)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Registration number "
                f"'{data.registration_number}' is already "
                f"registered"
            ),
        )

    firm.registration_number = data.registration_number
    updated_fields.append("registration_number")






if all(
    v is None
    for v in (
        data.name,
        data.registration_number,
        data.deals_on,
    )
):
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="At least one field must be provided for update",
    )





async def get_firm_by_registration_number(
    db: AsyncSession,
    registration_number: str,
) -> Firm | None:
    """Fetch a firm by its government registration number."""
    try:
        normalized = registration_number.strip()
        result = await db.execute(
            select(Firm).where(
                col(Firm.registration_number) == normalized
            )
        )
        return result.scalars().first()
    except Exception:
        logger.exception(
            "Failed to fetch firm registration_number=%s",
            registration_number,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )





