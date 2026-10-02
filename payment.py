# ------------------------------------------------------------------
# Helper — add near the top of the file
# ------------------------------------------------------------------

def firm_id_of(firm: Firm) -> int:
    """
    Return the firm's primary key as an int.

    SQLModel's type checker sees `.id` as `int | None` even after
    the row is loaded. This helper narrows the type and raises if
    the invariant is violated.
    """
    if firm.id is None:
        logger.error("Firm row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )
    return firm.id



async def get_firm_by_slug(
    db: AsyncSession,
    slug: str,
) -> Firm | None:
    """
    Fetch a firm by slug.

    Slugs are derived directly from the firm name
    (via `generate_slug(name)`) — they carry no id prefix, so a
    single indexed lookup is all that's needed.

    Returns None if not found — callers decide whether that's a 404.
    """
    try:
        normalized = slug.strip().lower()
        result = await db.execute(
            select(Firm).where(Firm.slug == normalized)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )


# api/firms/logics.py
"""Firm business logic."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.core.slug import generate_slug, parse_slug
from api.core.validators import normalize_firm_name
from api.firms.models import Firm
from api.firms.schemas import (
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
)
from api.receipts.models import Receipt
from api.users.schemas import ReadUser

logger = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================

async def get_firm_by_slug(
    db: AsyncSession,
    slug: str,
) -> Firm | None:
    """
    Fetch a firm by slug.

    Slug format is `{id}-{slugified-name}` (via generate_slug), so
    the numeric prefix allows a fast PK lookup before falling back
    to a slug query.

    Returns None if not found — callers decide whether that's a 404.
    """
    try:
        firm_id = parse_slug(slug)
        if firm_id is not None:
            firm = await db.get(Firm, firm_id)
            if firm and firm.slug == slug.strip().lower():
                return firm

        result = await db.execute(
            select(Firm).where(Firm.slug == slug.strip().lower())
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )


async def get_firm_by_name(
    db: AsyncSession,
    name: str,
) -> Firm | None:
    """
    Fetch a firm by normalized name.

    Caller passes the raw name; we normalize here so the query is
    case-insensitive and whitespace-collapsed consistently.
    """
    try:
        normalized = normalize_firm_name(name)
        result = await db.execute(
            select(Firm).where(Firm.name == normalized)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm name=%s", name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )


async def _count_receipts(
    db: AsyncSession,
    firm_id: int,
) -> int:
    """
    Count receipts belonging to a firm.

    Best-effort — returns 0 on error rather than failing the whole
    request, since the count is metadata and shouldn't block reads.
    """
    try:
        return (
            await db.execute(
                select(func.count())
                .select_from(Receipt)
                .where(Receipt.firm_id == firm_id)
            )
        ).scalar() or 0
    except Exception:
        logger.exception(
            "Failed to count receipts for firm_id=%s", firm_id
        )
        return 0


async def _counts_by_firm(
    db: AsyncSession,
    firm_ids: list[int],
) -> dict[int, int]:
    """
    Batch receipt counts for a list of firm IDs.

    One query, grouped. Returns {firm_id: count}.
    """
    if not firm_ids:
        return {}
    try:
        result = await db.execute(
            select(Receipt.firm_id, func.count(Receipt.id))
            .where(Receipt.firm_id.in_(firm_ids))
            .group_by(Receipt.firm_id)
        )
        return dict(result.all())
    except Exception:
        logger.exception(
            "Failed to batch-count receipts for %s firms", len(firm_ids)
        )
        return {}


# =============================================================================
# CREATE
# =============================================================================

async def create_firm(
    data: FirmCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Create a firm profile. A user may own multiple firms."""

    user_id = current_user.id

    # ------------------------------------------------------------------
    # Name uniqueness (global)
    # ------------------------------------------------------------------
    existing_name = await get_firm_by_name(db, data.name)
    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Firm '{data.name}' already exists",
        )

    normalized = normalize_firm_name(data.name)

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
        name=normalized,
        slug=generate_slug(normalized),
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
        counts = await _counts_by_firm(db, [f.id for f in firms])
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s", current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load your firms. Please try again.",
        )

    reads = []
    for firm in firms:
        read = FirmRead.model_validate(firm)
        read.receipt_count = counts.get(firm.id, 0)
        reads.append(read)

    return FirmListRead(total=len(reads), firms=reads)


async def read_all_firms(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> FirmListRead:
    """
    List every firm in the database.

    Route-level `AdminUser` dependency enforces admin-only access.
    This service has no user context by design.
    """
    try:
        total: int = (
            await db.execute(select(func.count()).select_from(Firm))
        ).scalar() or 0

        result = await db.execute(
            select(Firm).order_by(Firm.name).offset(skip).limit(limit)
        )
        firms = result.scalars().all()
        counts = await _counts_by_firm(db, [f.id for f in firms])
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list firms")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firms. Please try again.",
        )

    reads = []
    for firm in firms:
        read = FirmRead.model_validate(firm)
        read.receipt_count = counts.get(firm.id, 0)
        reads.append(read)

    return FirmListRead(total=total, firms=reads)


async def read_single_firm(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmRead:
    """
    Get a single firm by slug.

    Access:
        - owner of the firm
        - any admin

    Non-owners get 403 (not 404) because the firm's existence is
    public via the list endpoint — hiding it here would be theater.
    """
    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    read = FirmRead.model_validate(firm)
    read.receipt_count = await _count_receipts(db, firm.id)
    return read


# =============================================================================
# UPDATE
# =============================================================================

async def update_firm(
    slug: str,
    data: FirmUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Update a firm. Owner or admin only."""

    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

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

    updated_fields: list[str] = []

    # --- name (+ slug) ---
    if data.name is not None and data.name != firm.name:
        existing = await get_firm_by_name(db, data.name)
        if existing is not None and existing.id != firm.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Firm name '{data.name}' is already taken",
            )

        old_slug = firm.slug
        normalized = normalize_firm_name(data.name)
        firm.name = normalized
        firm.slug = generate_slug(normalized)
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

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

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

    read = FirmRead.model_validate(firm)
    read.receipt_count = await _count_receipts(db, firm.id)

    return {
        "message": f"Firm '{firm.name}' updated successfully",
        "firm": read,
    }


# =============================================================================
# DELETE
# =============================================================================

async def delete_firm(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a firm. Owner or admin only.

    Cascade: all receipts belonging to this firm are deleted via
    the FK constraint on receipts.firm_id (ondelete="CASCADE").

    Returns { message, receipts_deleted } so the frontend can tell
    the user exactly what was removed.
    """

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
    receipts_deleted = await _count_receipts(db, firm.id)

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
        "Firm '%s' deleted by user_id=%s (receipts_deleted=%s)",
        firm_name,
        current_user.id,
        receipts_deleted,
    )

    return {
        "message": f"Firm '{firm_name}' deleted successfully",
        "receipts_deleted": receipts_deleted,
 }




# api/firms/routes.py

from fastapi import APIRouter, status

from api.core.database import DBDep
from api.firms.logics import (
    create_firm,
    delete_firm,
    read_all_firms,
    read_my_firms,
    read_single_firm,
    update_firm,
)
from api.firms.schemas import (
    CreateFirmResponse,
    DeleteFirmResponse,
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
    UpdateFirmResponse,
)
from api.users.deps import AdminUser, CurrentUser

router = APIRouter(prefix="/firms", tags=["Firms"])


# =============================================================================
# AUTHENTICATED READS — OWNER ONLY
#
# Declared BEFORE /{slug} so /firms/me/mine matches here and not
# the dynamic route.
# =============================================================================

@router.get(
    "/me/mine",
    response_model=FirmListRead,
    status_code=status.HTTP_200_OK,
    summary="List firms owned by the authenticated user",
)
async def list_my_firms(db: DBDep, current_user: CurrentUser):
    return await read_my_firms(db=db, current_user=current_user)


# =============================================================================
# ADMIN-ONLY READ
# =============================================================================

@router.get(
    "",
    response_model=FirmListRead,
    status_code=status.HTTP_200_OK,
    summary="List all firms (admin only)",
)
async def list_all_firms(
    db: DBDep,
    admin: AdminUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_firms(db=db, skip=skip, limit=limit)


# =============================================================================
# PUBLIC-ISH READ — OWNER OR ADMIN
#
# Not truly public: the service layer enforces ownership. But the
# route itself is available to any authenticated user so non-owners
# get a proper 403 instead of an ambiguous 404.
# =============================================================================

@router.get(
    "/{slug}",
    response_model=FirmRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single firm (owner or admin)",
)
async def get_firm(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_single_firm(
        slug=slug, db=db, current_user=current_user
    )


# =============================================================================
# AUTHENTICATED MUTATIONS — OWNER OR ADMIN
# =============================================================================

@router.post(
    "",
    response_model=CreateFirmResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a firm (authenticated)",
)
async def create(
    data: FirmCreate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await create_firm(data=data, db=db, current_user=current_user)


@router.patch(
    "/{slug}",
    response_model=UpdateFirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a firm (owner or admin)",
)
async def patch_firm(
    slug: str,
    data: FirmUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_firm(
        slug=slug, data=data, db=db, current_user=current_user
    )


@router.delete(
    "/{slug}",
    response_model=DeleteFirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a firm (owner or admin) — cascades receipts",
)
async def remove_firm(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await delete_firm(
        slug=slug, db=db, current_user=current_user
 )



/firms/me/mine     ← literal, MUST be first
/firms             ← literal (no path param)
/firms/{slug}      ← dynamic, MUST be last
