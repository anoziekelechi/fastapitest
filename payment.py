# api/firms/models.py (additions to the Firm class)

disabled: bool = Field(
    default=False,
    sa_column=Column(
        Boolean,
        nullable=False,
        server_default="false",
        index=True,
    ),
)



# api/firms/schemas.py (additions)

class FirmAction(BaseModel):
    """Body for enable/disable firm actions (admin)."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=200)


class FirmAdminActionResponse(BaseModel):
    message: str
    firm: FirmRead



class FirmRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    slug: str | None = None
    registration_number: str | None = None
    address: str
    phone_number: str
    deals_on: str
    created_at: datetime
    updated_at: datetime

    disabled: bool = False              # ← new
    receipt_count: int = 0   

    
    
    # (already added)

# api/firms/logics.py

from api.users.logics import has_permission


# =============================================================================
# STATE GUARDS
#
# Every firm operation that touches money (receipts, firm mutations,
# firm reads) checks three things:
#
#   1. user.disabled  → caller's account is suspended
#   2. user.verified  → caller never verified their email
#   3. firm.disabled  → the target firm is suspended
#
# Each raises a specific error code the frontend uses to route the
# user to the correct recovery page.
# =============================================================================

def _guard_user_state(current_user: ReadUser) -> None:
    """Reject disabled or unverified callers."""
    if current_user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is suspended. Please contact admin.",
            headers={"X-Error-Code": "account_suspended"},
        )
    if not current_user.verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is not verified. Please verify your email.",
            headers={"X-Error-Code": "account_unverified"},
        )


def _guard_firm_state(firm: Firm) -> None:
    """Reject operations on a disabled firm."""
    if firm.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Firm '{firm.name}' is suspended. "
                f"Please contact admin."
            ),
            headers={"X-Error-Code": "firm_suspended"},
        )


# =============================================================================
# ENABLE / DISABLE
# =============================================================================

async def disable_firm(
    data: FirmAction,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Disable a firm (admin / manage_firms only).

    A disabled firm cannot:
        - create receipts
        - be updated
        - be deleted
        - be read in detail
    """
    await has_permission(
        user=current_user,
        required_perm="manage_firms",
    )

    firm = await get_firm_by_name(db, data.name)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{data.name}' not found",
        )

    if firm.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Firm '{firm.name}' is already disabled",
        )

    firm.disabled = True
    db.add(firm)

    try:
        await db.commit()
        await db.refresh(firm)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to disable firm name=%s", data.name
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disable firm. Please try again.",
        )

    logger.info(
        "Firm '%s' disabled by user_id=%s",
        firm.name,
        current_user.id,
    )

    read = FirmRead.model_validate(firm)
    read.receipt_count = await _count_receipts(db, firm_id_of(firm))

    return {
        "message": f"Firm '{firm.name}' has been disabled",
        "firm": read,
    }


async def enable_firm(
    data: FirmAction,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Re-enable a disabled firm (admin / manage_firms only)."""
    await has_permission(
        user=current_user,
        required_perm="manage_firms",
    )

    firm = await get_firm_by_name(db, data.name)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{data.name}' not found",
        )

    if not firm.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Firm '{firm.name}' is already active",
        )

    firm.disabled = False
    db.add(firm)

    try:
        await db.commit()
        await db.refresh(firm)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to enable firm name=%s", data.name
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enable firm. Please try again.",
        )

    logger.info(
        "Firm '%s' re-enabled by user_id=%s",
        firm.name,
        current_user.id,
    )

    read = FirmRead.model_validate(firm)
    read.receipt_count = await _count_receipts(db, firm_id_of(firm))

    return {
        "message": f"Firm '{firm.name}' has been re-activated",
        "firm": read,
    }



# api/firms/logics.py

async def create_firm(
    data: FirmCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a firm profile. A user may own multiple firms.

    Guarded:
        - caller must be verified
        - caller must not be disabled
    """

    # ------------------------------------------------------------------
    # User-state guard — disabled or unverified users cannot create firms
    # ------------------------------------------------------------------
    _guard_user_state(current_user)

    user_id = current_user.id

    # ------------------------------------------------------------------
    # Normalize ONCE — every downstream use references this value
    # ------------------------------------------------------------------
    normalized_name = normalize_firm_name(data.name)

    # ------------------------------------------------------------------
    # Name uniqueness (global) — query on the normalized value
    # ------------------------------------------------------------------
    existing_name = (
        await db.execute(
            select(Firm).where(Firm.name == normalized_name)
        )
    ).scalars().first()

    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Firm '{data.name}' already exists",
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
    # Persist — reuse `normalized_name`, don't recompute
    # ------------------------------------------------------------------
    firm = Firm(
        user_id=user_id,
        name=normalized_name,
        slug=generate_slug(normalized_name),
        registration_number=data.registration_number,
        address=data.address,
        phone_number=data.phone_number,
        deals_on=data.deals_on,
        disabled=False,   # ← explicit — new firms always start enabled
    )

    try:
        db.add(firm)
        await db.commit()
        await db.refresh(firm)
    except Exception:
        await db.rollback()
        logger.exception("Failed to create firm for user_id=%s", user_id)
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
