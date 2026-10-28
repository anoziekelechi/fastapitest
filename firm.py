""
this is our working schemas we are working on our last chart each routes returns a respose schema so frontend can 
mirrow it
""
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
    
    # Counters
    branch_count: int = 0
    


class FirmListRead(BaseModel):
    total: int
    firms: list[FirmRead]
    
    
     
    
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




# this is my logics

# =============================================================================
# ID NARROWING
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







async def delete_firm(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    guard_user_state(current_user)

    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )
    if not firm.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    result = await require_firm_access(db=db,firm_id=firm.id,current_user=current_user)

    # if firm.user_id != current_user.id and not current_user.is_admin:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="You can only delete your own firm",
    #         headers={"X-Error-Code": "wrong_permission"},
    #     )

    firm_name = firm.name
    fid = firm_id_of(result)

    receipts_deleted = await count_receipts(db, fid)
    branches_deleted = await count_branches(db, fid)

    try:
        await db.delete(result)
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
    read.branch_count = await count_branches(db, fid)
    #read.receipt_count = await count_receipts(db, fid)

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
    read.branch_count = await count_branches(db, fid)
    read.receipt_count = await count_receipts(db, fid)

    return {
        "message": f"Firm '{firm.name}' has been re-activated",
        "firm": read,
    }

# =============================================================================
# BRANCH — CREATE
# =============================================================================

async def create_branch(
    firm_slug: str,
    data: BranchCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    guard_user_state(current_user)

    firm = await get_firm_by_slug(db, firm_slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    guard_firm_state(firm)

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
        branch_no=data.branch_no, #type:ignore
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
    guard_user_state(current_user)

    firm = await get_firm_by_slug(db, firm_slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    result = await require_firm_access(firm_id=firm.id,current_user=current_user, db=db)

  

    fid = firm_id_of(result)

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
    guard_user_state(current_user)

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

    #guard_firm_state(firm)
    result = await require_firm_access(db=db,firm_id=firm.id,current_user=current_user)

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
#Cannot assign to attribute "branch_no" for class "Branch"
#Cannot assign to attribute "branch_no" for class "Branch
  #Type "int | None" is not assignable to type "str
    branch.branch_no = data.branch_no #error 
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
    guard_user_state(current_user)

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

    #guard_firm_state(firm)
    result = await require_firm_access(db=db,firm_id=firm.id,current_user=current_user)

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




