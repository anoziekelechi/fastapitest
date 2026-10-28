
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








    

        
   

