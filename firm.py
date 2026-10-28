# use only read my firm

# api/firms/logics.py
"""Firm business logic."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.core.slug import generate_slug
from api.core.validators import normalize_firm_name
from api.firms.models import Branch, Firm
from api.firms.schemas import (
    BranchCreate,
    BranchListRead,
    BranchRead,
    BranchUpdate,
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
)
from api.receipts.models import Receipt
from api.users.logics import has_permission
from api.users.schemas import ReadUser

logger = logging.getLogger(__name__)


# =============================================================================
# ID NARROWING
# =============================================================================

def firm_id_of(firm: Firm) -> int:
    if firm.id is None:
        logger.error("Firm row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )
    return firm.id


def branch_id_of(branch: Branch) -> int:
    if branch.id is None:
        logger.error("Branch row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load branch. Please try again.",
        )
    return branch.id


# =============================================================================
# STATE GUARDS
# =============================================================================

def _guard_user_state(current_user: ReadUser) -> None:
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
    if firm.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Firm '{firm.name}' is suspended. Please contact admin.",
            headers={"X-Error-Code": "firm_suspended"},
        )


# =============================================================================
# HELPERS
# =============================================================================

async def get_firm_by_slug(
    db: AsyncSession,
    slug: str,
) -> Firm | None:
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


async def get_firm_by_name(
    db: AsyncSession,
    name: str,
) -> Firm | None:
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


async def _count_receipts(db: AsyncSession, firm_id: int) -> int:
    try:
        return (
            await db.execute(
                select(func.count())
                .select_from(Receipt)
                .join(Branch, col(Receipt.branch_id) == col(Branch.id))
                .where(col(Branch.firm_id) == firm_id)
            )
        ).scalar() or 0
    except Exception:
        logger.exception("Failed to count receipts for firm_id=%s", firm_id)
        return 0


async def _count_branches(db: AsyncSession, firm_id: int) -> int:
    try:
        return (
            await db.execute(
                select(func.count())
                .select_from(Branch)
                .where(col(Branch.firm_id) == firm_id)
            )
        ).scalar() or 0
    except Exception:
        logger.exception("Failed to count branches for firm_id=%s", firm_id)
        return 0


async def _counts_by_firm(
    db: AsyncSession,
    firm_ids: list[int],
) -> tuple[dict[int, int], dict[int, int]]:
    """Batch branch + receipt counts for many firms."""
    if not firm_ids:
        return {}, {}

    try:
        branch_result = await db.execute(
            select(Branch.firm_id, func.count())
            .where(col(Branch.firm_id).in_(firm_ids))
            .group_by(col(Branch.firm_id))
        )
        branch_counts = {row[0]: int(row[1]) for row in branch_result.all()}

        receipt_result = await db.execute(
            select(Branch.firm_id, func.count(Receipt.id))
            .join(Receipt, col(Receipt.branch_id) == col(Branch.id))
            .where(col(Branch.firm_id).in_(firm_ids))
            .group_by(col(Branch.firm_id))
        )
        receipt_counts = {row[0]: int(row[1]) for row in receipt_result.all()}

        return branch_counts, receipt_counts
    except Exception:
        logger.exception(
            "Failed to batch-count for %s firms", len(firm_ids)
        )
        return {}, {}


# =============================================================================
# FIRM — CREATE
# =============================================================================

async def create_firm(
    data: FirmCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Create a firm. Caller must be verified and active."""
    _guard_user_state(current_user)

    existing = await get_firm_by_name(db, data.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Firm '{data.name}' already exists",
        )

    normalized_name = normalize_firm_name(data.name)

    firm = Firm(
        user_id=current_user.id,
        name=normalized_name,
        slug=generate_slug(normalized_name),
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
        "Firm '%s' created by user_id=%s", firm.name, current_user.id
    )

    return {
        "message": f"Firm '{firm.name}' created successfully",
        "firm": FirmRead.model_validate(firm),
    }


# =============================================================================
# FIRM — READ
# =============================================================================

async def read_my_firms(
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmListRead:
    _guard_user_state(current_user)

    try:
        result = await db.execute(
            select(Firm)
            .where(Firm.user_id == current_user.id)
            .order_by(col(Firm.name))
        )
        firms = result.scalars().all()
        ids = [firm_id_of(f) for f in firms]
        branch_counts, receipt_counts = await _counts_by_firm(db, ids)
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
        fid = firm_id_of(firm)
        read = FirmRead.model_validate(firm)
        read.branch_count = branch_counts.get(fid, 0)
        read.receipt_count = receipt_counts.get(fid, 0)
        reads.append(read)

    return FirmListRead(total=len(reads), firms=reads)


async def read_all_firms(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> FirmListRead:
    """Admin-only via route-level `AdminUser` dependency."""
    try:
        total: int = (
            await db.execute(select(func.count()).select_from(Firm))
        ).scalar() or 0

        result = await db.execute(
            select(Firm)
            .order_by(col(Firm.name))
            .offset(skip)
            .limit(limit)
        )
        firms = result.scalars().all()
        ids = [firm_id_of(f) for f in firms]
        branch_counts, receipt_counts = await _counts_by_firm(db, ids)
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
        fid = firm_id_of(firm)
        read = FirmRead.model_validate(firm)
        read.branch_count = branch_counts.get(fid, 0)
        read.receipt_count = receipt_counts.get(fid, 0)
        reads.append(read)

    return FirmListRead(total=total, firms=reads)


async def read_single_firm(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmRead:
    """Owner or admin only."""
    _guard_user_state(current_user)

    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    _guard_firm_state(firm)

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    fid = firm_id_of(firm)
    read = FirmRead.model_validate(firm)
    read.branch_count = await _count_branches(db, fid)
    read.receipt_count = await _count_receipts(db, fid)
    return read


# =============================================================================
# FIRM — UPDATE
# =============================================================================

async def update_firm(
    slug: str,
    data: FirmUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    _guard_user_state(current_user)

    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    _guard_firm_state(firm)

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    if all(v is None for v in (data.name, data.deals_on)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    updated_fields: list[str] = []

    if data.name is not None and data.name != firm.name:
        existing = await get_firm_by_name(db, data.name)
        if existing is not None and firm_id_of(existing) != firm_id_of(firm):
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

    fid = firm_id_of(firm)
    read = FirmRead.model_validate(firm)
    read.branch_count = await _count_branches(db, fid)
    read.receipt_count = await _count_receipts(db, fid)

    return {
        "message": f"Firm '{firm.name}' updated successfully",
        "firm": read,
    }


# =============================================================================
# FIRM — DELETE (cascades branches, then receipts via branch cascade)
# =============================================================================

async def delete_firm(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    _guard_user_state(current_user)

    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    _guard_firm_state(firm)

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    firm_name = firm.name
    fid = firm_id_of(firm)

    receipts_deleted = await _count_receipts(db, fid)
    branches_deleted = await _count_branches(db, fid)

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
        "Firm '%s' deleted by user_id=%s "
        "(branches_deleted=%s, receipts_deleted=%s)",
        firm_name,
        current_user.id,
        branches_deleted,
        receipts_deleted,
    )

    return {
        "message": f"Firm '{firm_name}' deleted successfully",
        "receipts_deleted": receipts_deleted,
        "branches_deleted": branches_deleted,
    }


# =============================================================================
# FIRM — DISABLE / ENABLE
# =============================================================================

async def disable_firm(
    data: FirmAction,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    await has_permission(user=current_user, required_perm="manage_firms")

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
        logger.exception("Failed to disable firm name=%s", data.name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disable firm. Please try again.",
        )

    fid = firm_id_of(firm)
    read = FirmRead.model_validate(firm)
    read.branch_count = await _count_branches(db, fid)
    read.receipt_count = await _count_receipts(db, fid)

    return {
        "message": f"Firm '{firm.name}' has been disabled",
        "firm": read,
    }


async def enable_firm(
    data: FirmAction,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    await has_permission(user=current_user, required_perm="manage_firms")

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
        logger.exception("Failed to enable firm name=%s", data.name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enable firm. Please try again.",
        )

    fid = firm_id_of(firm)
    read = FirmRead.model_validate(firm)
    read.branch_count = await _count_branches(db, fid)
    read.receipt_count = await _count_receipts(db, fid)

    return {
        "message": f"Firm '{firm.name}' has been re-activated",
        "firm": read,
    }


# =============================================================================
# BRANCH — HELPERS
# =============================================================================

async def get_branch_by_id(
    db: AsyncSession,
    branch_id: int,
) -> Branch | None:
    try:
        return await db.get(Branch, branch_id)
    except Exception:
        logger.exception("Failed to fetch branch id=%s", branch_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load branch. Please try again.",
        )


# =============================================================================
# BRANCH — CREATE
# =============================================================================

async def create_branch(
    firm_slug: str,
    data: BranchCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    _guard_user_state(current_user)

    firm = await get_firm_by_slug(db, firm_slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    _guard_firm_state(firm)

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only add branches to your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    fid = firm_id_of(firm)

    try:
        existing = (
            await db.execute(
                select(Branch).where(
                    col(Branch.firm_id) == fid,
                    col(Branch.branch_no) == data.branch_no,
                )
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check duplicate branch_no=%s firm_id=%s",
            data.branch_no,
            fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create branch. Please try again.",
        )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch {data.branch_no} already exists "
                f"for '{firm.name}'"
            ),
        )

    branch = Branch(
        firm_id=fid,
        branch_no=data.branch_no,
        address=data.address,
        phone_number=data.phone_number,
    )

    try:
        db.add(branch)
        await db.commit()
        await db.refresh(branch)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create branch for firm_id=%s", fid
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create branch. Please try again.",
        )

    logger.info(
        "Branch %s created for firm '%s' by user_id=%s",
        branch.branch_no,
        firm.name,
        current_user.id,
    )

    return {
        "message": (
            f"Branch {branch.branch_no} created successfully "
            f"for '{firm.name}'"
        ),
        "branch": BranchRead.model_validate(branch),
    }


# =============================================================================
# BRANCH — READ
# =============================================================================

async def read_branches_for_firm(
    firm_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> BranchListRead:
    _guard_user_state(current_user)

    firm = await get_firm_by_slug(db, firm_slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    _guard_firm_state(firm)

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view branches of your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    fid = firm_id_of(firm)

    try:
        result = await db.execute(
            select(Branch)
            .where(col(Branch.firm_id) == fid)
            .order_by(col(Branch.branch_no))
        )
        branches = result.scalars().all()
    except Exception:
        logger.exception("Failed to load branches for firm_id=%s", fid)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load branches. Please try again.",
        )

    return BranchListRead(
        firm_id=fid,
        firm_name=firm.name,
        total=len(branches),
        branches=[BranchRead.model_validate(b) for b in branches],
    )


# =============================================================================
# BRANCH — UPDATE
# =============================================================================

async def update_branch(
    branch_id: int,
    data: BranchUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    _guard_user_state(current_user)

    branch = await get_branch_by_id(db, branch_id)
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Branch id={branch_id} not found",
        )

    firm = await db.get(Firm, branch.firm_id)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Firm not found for this branch",
        )

    _guard_firm_state(firm)

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update branches of your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    if all(
        v is None
        for v in (data.branch_no, data.address, data.phone_number)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    updated_fields: list[str] = []

    if data.branch_no is not None and data.branch_no != branch.branch_no:
        try:
            existing = (
                await db.execute(
                    select(Branch).where(
                        col(Branch.firm_id) == branch.firm_id,
                        col(Branch.branch_no) == data.branch_no,
                        col(Branch.id) != branch_id_of(branch),
                    )
                )
            ).scalars().first()
        except Exception:
            logger.exception(
                "Failed to check duplicate branch_no=%s", data.branch_no
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update branch. Please try again.",
            )

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Branch {data.branch_no} already exists "
                    f"for '{firm.name}'"
                ),
            )

        branch.branch_no = data.branch_no
        updated_fields.append("branch_no")

    if data.address is not None and data.address != branch.address:
        branch.address = data.address
        updated_fields.append("address")

    if (
        data.phone_number is not None
        and data.phone_number != branch.phone_number
    ):
        branch.phone_number = data.phone_number
        updated_fields.append("phone_number")

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    try:
        db.add(branch)
        await db.commit()
        await db.refresh(branch)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to update branch id=%s", branch_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update branch. Please try again.",
        )

    return {
        "message": (
            f"Branch {branch.branch_no} updated successfully"
        ),
        "branch": BranchRead.model_validate(branch),
    }


# =============================================================================
# BRANCH — DELETE
# =============================================================================

async def delete_branch(
    branch_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a branch. Refused if the branch has issued receipts
    (FK is RESTRICT).
    """
    _guard_user_state(current_user)

    branch = await get_branch_by_id(db, branch_id)
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Branch id={branch_id} not found",
        )

    firm = await db.get(Firm, branch.firm_id)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Firm not found for this branch",
        )

    _guard_firm_state(firm)

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete branches of your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    # Refuse if receipts exist
    try:
        receipt_count: int = (
            await db.execute(
                select(func.count())
                .select_from(Receipt)
                .where(col(Receipt.branch_id) == branch_id)
            )
        ).scalar() or 0
    except Exception:
        logger.exception(
            "Failed to count receipts for branch_id=%s", branch_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete branch. Please try again.",
        )

    if receipt_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch {branch.branch_no} has {receipt_count} "
                f"{'receipt' if receipt_count == 1 else 'receipts'}. "
                f"Delete or transfer them before removing the branch."
            ),
        )

    branch_no = branch.branch_no

    try:
        await db.delete(branch)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete branch id=%s", branch_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete branch. Please try again.",
        )

    logger.info(
        "Branch %s (firm '%s') deleted by user_id=%s",
        branch_no,
        firm.name,
        current_user.id,
    )

    return {
        "message": f"Branch {branch_no} deleted successfully"
        }
"""User schemas."""
#from locale import currency
import re
from decimal import Decimal
from datetime import datetime
from typing import Literal,Self
from pydantic_core.core_schema import FieldValidationInfo

from pydantic import(
    BaseModel, 
    EmailStr, 
    Field, 
    ConfigDict, 
    field_validator,
    model_validator,
    )
from api.core.validators import (
    normalize_email, 
    normalize_firm_name,
    validate_local_phone,
    sanitize_alphanumeric_text_only,
    normalize_user_message,
    normalize_payment_option
    )


    
# =============================================================================
# FIRM — REQUEST
# =============================================================================

class FirmCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=200)
    registration_number:str | None = Field(default=None,min_length=2,max_length=50)
    deals_on: str = Field(..., min_length=10, max_length=2000)

    @field_validator("name", mode="before")
    @classmethod
    def validate_firm_name(cls, v: str) -> str:
        return normalize_firm_name(v)
    
    @field_validator("deals_on", mode="before")
    @classmethod
    def validate_deal_on_fields(cls, v: str, info: FieldValidationInfo) -> str:
        # info.field_name automatically passes "name" to your error message!
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ").capitalize()
        return normalize_user_message(v, field_name=display_name)

         


class FirmUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=2, max_length=200)
    registration_number:str | None = Field(default=None,min_length=2,max_length=50)
    deals_on: str | None = Field(None, min_length=10, max_length=2000)

    @field_validator("name", mode="before")
    @classmethod
    def validate_firmname(cls, v: str | None) -> str | None:
    
        return normalize_firm_name(v) if v else None
    
    @field_validator("deals_on", mode="before")
    @classmethod
    def validate_deal_on_fields(cls, v: str | None, info: FieldValidationInfo) -> str | None:
        if v is None:
            return None
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ").capitalize()
        return normalize_user_message(v, field_name=display_name) 



class FirmAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=200)


# =============================================================================
# FIRM — READ
# =============================================================================
class BranchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    branch_no: int
    address: str
    phone_number: str
    created_at: datetime
    updated_at: datetime
    
    
class BranchListRead(BaseModel):
    firm_id: int
    firm_name: str
    total: int
    branches: list[BranchRead]

class FirmRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    registration_number: str | None = None
    slug: str | None = None
    deals_on: str
    disabled: bool
    created_at: datetime
    updated_at: datetime
    # nested branches populated by the service layer
    branches: list[BranchRead]= []
    # Counters
    branch_count: int = 0
    receipt_count: int = 0


class FirmListRead(BaseModel):
    total: int
    firms: list[FirmRead]
    
class DeleteFirmResponse(BaseModel):
    message: str
    receipts_deleted: int
    
# =============================================================================
# BRANCH — REQUEST
# =============================================================================

class BranchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_no: int = Field(..., gt=0)
    address: str = Field(..., min_length=5, max_length=500)
    phone_number: str

    @field_validator("address", mode="before")
    @classmethod
    def validate_address(cls, v: str) -> str:
        return sanitize_alphanumeric_text_only(v)

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return validate_local_phone(v)


class BranchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_no: int | None = Field(None, gt=0)
    address: str | None = Field(None, min_length=5, max_length=500)
    phone_number: str | None = None

    @field_validator("address", mode="before")
    @classmethod
    def validate_address(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return sanitize_alphanumeric_text_only(v)

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_local_phone(v)




# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreateFirmResponse(BaseModel):
    message: str
    firm: FirmRead


class UpdateFirmResponse(BaseModel):
    message: str
    firm: FirmRead


class FirmAdminActionResponse(BaseModel):
    message: str
    firm: FirmRead


class DeleteFirmResponse(BaseModel):
    message: str
    receipts_deleted: int
    branches_deleted: int


class CreateBranchResponse(BaseModel):
    message: str
    branch: BranchRead


class UpdateBranchResponse(BaseModel):
    message: str
    branch: BranchRead








# =============================================================================
# ID NARROWING
# =============================================================================

def firm_id_of(firm: Firm) -> int:
    if firm.id is None:
        logger.error("Firm row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )
    return firm.id


def branch_id_of(branch: Branch) -> int:
    if branch.id is None:
        logger.error("Branch row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load branch. Please try again.",
        )
    return branch.id

# =============================================================================
# HELPERS
# =============================================================================

async def get_firm_by_slug(
    db: AsyncSession,
    slug: str,
) -> Firm | None:
    """Fetch a payment method by its normalized slug."""

    normalized = slug.strip().lower()

    try:
        result = await db.execute(
            select(Firm).where(
                Firm.slug == normalized
            )
        )
        return result.scalars().first()

    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching firm: slug=%s",
            normalized,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        ) from exc



async def get_firm_by_name(
    db: AsyncSession,
    name: str,
) -> Firm | None:
    try:
        #normalized = normalize_firm_name(name)
        result = await db.execute(
            select(Firm).where(Firm.name == name)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm name=%s", name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )


async def count_receipts(db: AsyncSession, firm_id: int) -> int:
    try:
        return (
            await db.execute(
                select(func.count())
                .select_from(Receipt)
                .join(Branch, col(Receipt.branch_id) == col(Branch.id))
                .where(col(Branch.firm_id) == firm_id)
            )
        ).scalar() or 0
    except Exception:
        logger.exception("Failed to count receipts for firm_id=%s", firm_id)
        return 0





async def count_branches(db: AsyncSession, firm_id: int) -> int:
    try:
        return (
            await db.execute(
                select(func.count())
                .select_from(Branch)
                .where(col(Branch.firm_id) == firm_id)
            )
        ).scalar() or 0
    except Exception:
        logger.exception("Failed to count branches for firm_id=%s", firm_id)
        return 0


async def counts_by_firm(
    db: AsyncSession,
    firm_ids: list[int],
) -> tuple[dict[int, int], dict[int, int]]:
    """Batch branch + receipt counts for many firms."""
    if not firm_ids:
        return {}, {}

    try:
        branch_result = await db.execute(
            select(Branch.firm_id, func.count())
            .where(col(Branch.firm_id).in_(firm_ids))
            .group_by(col(Branch.firm_id))
        )
        branch_counts = {row[0]: int(row[1]) for row in branch_result.all()}

        receipt_result = await db.execute(
            select(Branch.firm_id, func.count())
            .join(Receipt, col(Receipt.branch_id) == col(Branch.id))
            .where(col(Branch.firm_id).in_(firm_ids))
            .group_by(col(Branch.firm_id))
        )
        receipt_counts = {row[0]: int(row[1]) for row in receipt_result.all()}

        return branch_counts, receipt_counts
    except Exception:
        logger.exception(
            "Failed to batch-count for %s firms", len(firm_ids)
        )
        return {}, {}



async def get_firm_by_registration_number(
    db: AsyncSession,
    registration_number: str,
) -> Firm | None:
    """Fetch a firm by its government registration number."""
    try:
        result = await db.execute(
            select(Firm).where(
                col(Firm.registration_number) == registration_number
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




# =============================================================================
# FIRM STATE — pure predicate on a loaded firm
# =============================================================================

def guard_firm_state(firm: Firm) -> None:
    """
    Reject operations on a disabled firm.

    Pure state check — does not load the firm, does not check ownership.
    Use when you already have the firm and only need the state check
    (e.g. after loading it inside a loop, or from a background task).
    """
    if firm.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Firm '{firm.name}' is suspended. Please contact admin.",
            headers={"X-Error-Code": "firm_suspended"},
        )


# =============================================================================
# FIRM ACCESS — load + ownership + optional state
# =============================================================================

async def require_firm_access(
    db: AsyncSession,
    *,
    firm_id: int,
    current_user: ReadUser,
    require_active: bool = True,
) -> Firm:
    """
    Load a firm and verify the caller can act on it.

    Steps:
        1. Load the firm (404 if missing).
        2. Reject if the caller is neither the owner nor an admin.
        3. When `require_active=True` (default), reject disabled firms.

    Does NOT check user state — callers that need it should call
    `guard_user_state(current_user)` first.

    Default is `require_active=True` because 99% of call sites are
    money-touching operations where acting on a disabled firm is wrong.
    The 1% that need to *read* a disabled firm should pass
    `require_active=False` explicitly, so the choice is deliberate.
    """
    from api.core.logging import logger  # or wherever your logger lives

    try:
        firm = await db.get(Firm, firm_id)
    except SQLAlchemyError as exc:
        logger.exception("Failed to load firm id=%s", firm_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        ) from exc

    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firm not found.",
        )

    if not is_admin(current_user) and firm.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this firm.",
            headers={"X-Error-Code": "wrong_permission"},
        )

    if require_active:
        guard_firm_state(firm)

    return firm






#update this to return only user firm and branch count
# example abc.com 2 branches
async def read_my_firms(
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmListRead:
    """
    Get all firms owned by the authenticated user.

    Each firm is returned with its full branch list nested under
    `firm.branches`, sorted by `branch_no` ascending.
    if no firm return message 
    """
    guard_user_state(current_user)

    try:
        result = await db.execute(
            select(Firm)
            .where(Firm.user_id == current_user.id)
            .order_by(col(Firm.name))
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

    if not firms:
        #return FirmListRead(total=0, firms=[]),
       #raise 404 instead
       
    
    
    

    firm_ids = [firm_id_of(f) for f in firms]

    # ------------------------------------------------------------------
    # Batch-load all branches for all of the caller's firms in one query
    # ------------------------------------------------------------------
    try:
        branches_result = await db.execute(
            select(Branch)
            .where(col(Branch.firm_id).in_(firm_ids))
            .order_by(
                col(Branch.firm_id).asc(),
                col(Branch.branch_no).asc(),
            )
        )
        all_branches = branches_result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load branches for user_id=%s", current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load your firms. Please try again.",
        )

    # Group branches by firm_id
    branches_by_firm: dict[int, list[Branch]] = {}
    for branch in all_branches:
        branches_by_firm.setdefault(branch.firm_id, []).append(branch)

    # ------------------------------------------------------------------
    # Batch-load receipt counts per firm
    # ------------------------------------------------------------------
    try:
        receipt_counts_result = await db.execute(
            select(Branch.firm_id, func.count())
            .join(Receipt, col(Receipt.branch_id) == col(Branch.id))
            .where(col(Branch.firm_id).in_(firm_ids))
            .group_by(col(Branch.firm_id))
        )
        receipt_counts = {
            row[0]: int(row[1]) for row in receipt_counts_result.all()
        }
    except Exception:
        logger.exception(
            "Failed to count receipts for user_id=%s", current_user.id
        )
        receipt_counts = {}

    # ------------------------------------------------------------------
    # Assemble
    # ------------------------------------------------------------------
    reads: list[FirmRead] = []
    for firm in firms:
        fid = firm_id_of(firm)
        branches = branches_by_firm.get(fid, [])

        read = FirmRead.model_validate(firm)
        read.branches = [
            BranchRead.model_validate(b) for b in branches
        ]
        read.branch_count = len(branches)
        read.receipt_count = receipt_counts.get(fid, 0)

        reads.append(read)

    return FirmListRead(total=len(reads), firms=reads)











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



export interface FirmRead {
  id: number;
  user_id: number;
  name: string;
  slug?: string | null;
  registration_number?: string | null;
  address: string;
  phone_number: string;
  deals_on: string;
  created_at: string;
  updated_at: string;

  /** Denormalized count of receipts belonging to this firm. */
  receipt_count: number;
}

export interface FirmListRead {
  total: number;
  firms: FirmRead[];
}



export interface DeleteFirmResponse {
  message: string;
  receipts_deleted: number;
}



export * from "./read";
export * from "./create";
export * from "./update";
export * from "./delete";

// src/pages/firms/FirmsList.tsx

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import axios from "axios";

import Container from "react-bootstrap/Container";
import ListGroup from "react-bootstrap/ListGroup";
import Spinner from "react-bootstrap/Spinner";
import Alert from "react-bootstrap/Alert";

import api from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import { handleApiError } from "@/lib/handleApiError";
import type { FirmListRead } from "@/types";

const FirmsList = () => {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [data, setData] = useState<FirmListRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isAdmin = Boolean(user?.is_admin);

  useEffect(() => {
    if (!user) return; // wait for auth state

    const controller = new AbortController();
    let mounted = true;

    // Admin → all firms. Everyone else → their own.
    const endpoint = isAdmin ? "/firms" : "/firms/me/mine";

    api
      .get<FirmListRead>(endpoint, { signal: controller.signal })
      .then((res) => {
        if (mounted) setData(res.data);
      })
      .catch((err) => {
        if (axios.isCancel(err)) return;
        if (!mounted) return;
        const msg = handleApiError(err, {
          navigate,
          redirectOnAuth: false,
          redirectOnForbidden: false,
        });
        if (msg) setError(msg);
      })
      .finally(() => {
        if (mounted && !controller.signal.aborted) setLoading(false);
      });

    return () => {
      mounted = false;
      controller.abort();
    };
  }, [navigate, isAdmin, user]);

  if (loading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" role="status" />
        <p className="mt-3 text-muted">Loading firms...</p>
      </Container>
    );
  }

  if (error) {
    return (
      <Container className="py-5" style={{ maxWidth: 720 }}>
        <Alert variant="danger" className="text-center">
          {error}
        </Alert>
      </Container>
    );
  }

  const firms = data?.firms ?? [];

  return (
    <Container className="py-4" style={{ maxWidth: 720 }}>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h1 className="h3 mb-0">
          {isAdmin ? "All Firms" : "My Firms"}
        </h1>
        <Link to="/firms/new" className="btn btn-primary">
          + Add Firm
        </Link>
      </div>

      {firms.length === 0 ? (
        <p className="text-center text-muted py-5">
          {isAdmin
            ? "No firms registered yet."
            : "You don't have any firms yet."}
        </p>
      ) : (
        <ListGroup>
          {firms.map((firm) => {
            const isMine = user?.id === firm.user_id;
            const canOpen = isMine || isAdmin;

            return canOpen ? (
              <ListGroup.Item
                key={firm.id}
                action
                as={Link}
                to={`/firms/${firm.slug}`}
                className="text-info d-flex justify-content-between"
              >
                <span>{firm.name}</span>
                <span className="text-muted small">
                  {firm.receipt_count}{" "}
                  {firm.receipt_count === 1 ? "receipt" : "receipts"}
                </span>
              </ListGroup.Item>
            ) : (
              <ListGroup.Item
                key={firm.id}
                className="d-flex justify-content-between"
              >
                <span>{firm.name}</span>
                <span className="text-muted small">
                  {firm.receipt_count}{" "}
                  {firm.receipt_count === 1 ? "receipt" : "receipts"}
                </span>
              </ListGroup.Item>
            );
          })}
        </ListGroup>
      )}
    </Container>
  );
};

export default FirmsList;



#updated  use this

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
# ORDERING RULE
#
# FastAPI matches routes top-to-bottom, first match wins.
# Literal paths MUST be declared before dynamic paths at the same level.
#
#     /firms/me/mine      ← literal       (before /{slug})
#     /firms              ← literal
#     /firms/{slug}       ← dynamic       (last)
#     /firms/{slug}/edit  ← dynamic+literal suffix (after /{slug})
#
# If you put /{slug} first, GET /firms/me/mine matches with slug="me"
# and returns 404.
# =============================================================================


# =============================================================================
# 1. LITERAL PATHS (fixed segments) — MUST come first
# =============================================================================

@router.get(
    "/me/mine",
    response_model=FirmListRead,
    status_code=status.HTTP_200_OK,
    summary="List firms owned by the authenticated user",
)
async def list_my_firms(
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_my_firms(db=db, current_user=current_user)


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
    return await create_firm(
        data=data, db=db, current_user=current_user
    )


# =============================================================================
# 2. DYNAMIC PATHS (path parameters) — MUST come last
#
# /{slug} matches any single segment, so it "swallows" any literal path
# declared after it. Declare it after every literal route.
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







# api/firms/schemas.py

class FirmAction(BaseModel):
    """Body for enable/disable firm actions (admin / manage_firms)."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=200)


class FirmAdminActionResponse(BaseModel):
    """Response for both enable and disable firm actions."""
    message: str
    firm: FirmRead





# api/firms/routes.py (additions)

from fastapi import APIRouter, status

from api.firms.logics import (
    create_firm,
    delete_firm,
    disable_firm,
    enable_firm,
    read_all_firms,
    read_my_firms,
    read_single_firm,
    update_firm,
)
from api.firms.schemas import (
    CreateFirmResponse,
    DeleteFirmResponse,
    FirmAction,
    FirmAdminActionResponse,
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
    UpdateFirmResponse,
)
from api.users.deps import AdminUser, CurrentUser

router = APIRouter(prefix="/firms", tags=["Firms"])


# =============================================================================
# ... existing routes unchanged ...


# =============================================================================
# ADMIN ACTIONS — enable / disable
#
# Both gated by `manage_firms` permission (checked in the service).
# Both declared BEFORE /{slug} so they don't collide with the dynamic
# path — `firm/disable` would otherwise match slug="disable".
#
# Both take the firm's NAME in the body, not the slug. Rationale:
#   - Admins act on suspended firms via admin listings where name is
#     the natural identifier.
#   - Slug can be regenerated when the name changes; using name avoids
#     stale-slug lookups.
# =============================================================================

@router.post(
    "/disable",
    response_model=FirmAdminActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable a firm (admin / manage_firms)",
)
async def disable_firm_route(
    data: FirmAction,
    db: DBDep,
    current_user: CurrentUser,
):
    return await disable_firm(
        data=data,
        db=db,
        current_user=current_user,
    )


@router.post(
    "/enable",
    response_model=FirmAdminActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Re-enable a disabled firm (admin / manage_firms)",
)
async def enable_firm_route(
    data: FirmAction,
    db: DBDep,
    current_user: CurrentUser,
):
    return await enable_firm(
        data=data,
        db=db,
        current_user=current_user,
    )


# 1. Literal paths — fixed segments
@router.get("/me/mine", ...)         # list my firms
@router.get("", ...)                 # list all firms (admin)
@router.post("", ...)                # create firm
@router.post("/disable", ...)        # ← new — admin action
@router.post("/enable", ...)         # ← new — admin action

# 2. Dynamic paths — path parameters
@router.get("/{slug}", ...)          # get firm by slug
@router.patch("/{slug}", ...)        # update firm by slug
@router.delete("/{slug}", ...)       # delete firm by slug





#firm with branch
# api/firms/routes.py

from fastapi import APIRouter, status

from api.core.database import DBDep
from api.firms.logics import (
    create_branch,
    create_firm,
    delete_branch,
    delete_firm,
    disable_firm,
    enable_firm,
    read_all_firms,
    read_branches_for_firm,
    read_my_firms,
    read_single_firm,
    update_branch,
    update_firm,
)
from api.firms.schemas import (
    BranchCreate,
    BranchListRead,
    BranchUpdate,
    CreateBranchResponse,
    CreateFirmResponse,
    DeleteFirmResponse,
    FirmAction,
    FirmAdminActionResponse,
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
    MessageResponse,
    UpdateBranchResponse,
    UpdateFirmResponse,
)
from api.users.deps import AdminUser, CurrentUser

router = APIRouter(prefix="/firms", tags=["Firms"])


# =============================================================================
# ORDERING: literals before dynamic
# =============================================================================

# --- FIRM (literal) ---

@router.get("/me/mine", response_model=FirmListRead)
async def list_my_firms(db: DBDep, current_user: CurrentUser):
    return await read_my_firms(db=db, current_user=current_user)


@router.get("", response_model=FirmListRead)
async def list_all_firms(
    db: DBDep,
    admin: AdminUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_firms(db=db, skip=skip, limit=limit)


@router.post("", response_model=CreateFirmResponse, status_code=status.HTTP_201_CREATED)
async def create(data: FirmCreate, db: DBDep, current_user: CurrentUser):
    return await create_firm(data=data, db=db, current_user=current_user)


@router.post("/disable", response_model=FirmAdminActionResponse)
async def disable(data: FirmAction, db: DBDep, current_user: CurrentUser):
    return await disable_firm(data=data, db=db, current_user=current_user)


@router.post("/enable", response_model=FirmAdminActionResponse)
async def enable(data: FirmAction, db: DBDep, current_user: CurrentUser):
    return await enable_firm(data=data, db=db, current_user=current_user)


# --- BRANCH (nested, literal-prefixed) ---

@router.get(
    "/{firm_slug}/branches",
    response_model=BranchListRead,
)
async def list_branches(
    firm_slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_branches_for_firm(
        firm_slug=firm_slug, db=db, current_user=current_user
    )


@router.post(
    "/{firm_slug}/branches",
    response_model=CreateBranchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_branch(
    firm_slug: str,
    data: BranchCreate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await create_branch(
        firm_slug=firm_slug,
        data=data,
        db=db,
        current_user=current_user,
    )


@router.patch(
    "/branches/{branch_id}",
    response_model=UpdateBranchResponse,
)
async def patch_branch(
    branch_id: int,
    data: BranchUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_branch(
        branch_id=branch_id,
        data=data,
        db=db,
        current_user=current_user,
    )


@router.delete(
    "/branches/{branch_id}",
    response_model=MessageResponse,
)
async def remove_branch(
    branch_id: int,
    db: DBDep,
    current_user: CurrentUser,
):
    return await delete_branch(
        branch_id=branch_id, db=db, current_user=current_user
    )


# --- FIRM (dynamic, LAST) ---

@router.get("/{slug}", response_model=FirmRead)
async def get_firm(slug: str, db: DBDep, current_user: CurrentUser):
    return await read_single_firm(
        slug=slug, db=db, current_user=current_user
    )


@router.patch("/{slug}", response_model=UpdateFirmResponse)
async def patch_firm(
    slug: str,
    data: FirmUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_firm(
        slug=slug, data=data, db=db, current_user=current_user
    )


@router.delete("/{slug}", response_model=DeleteFirmResponse)
async def remove_firm(slug: str, db: DBDep, current_user: CurrentUser):
    return await delete_firm(
        slug=slug, db=db, current_user=current_user
    )



    

    








    

        
   

