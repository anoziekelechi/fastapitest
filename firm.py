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






# =============================================================================
# FIRM — CREATE
# =============================================================================
async def create_firm(
    data:FirmCreate,
    db: AsyncSession,
    firm_logo_key: UploadFile | None,
    current_user: ReadUser,
) -> dict:
    
    guard_user_state(current_user)
    
    """Create a firm. Caller must be verified and active
    
    - firm names are globally unique.
    - The logo is optional and stored in Cloudflare R2.

    A failed database transaction triggers rollback and best-effort
    cleanup of the newly uploaded logo.
    """

    # ------------------------------------------------------------------
    # 1. Check for an existing firm name
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(Firm).where(
                col(Firm.name) == data.name,
            )
        )
        existing = result.scalars().first()

    except SQLAlchemyError as exc:
        await db.rollback()

        logger.exception(
            "Database error checking firm name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create firm. Please try again.",
        ) from exc

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"firm with name '{data.name}' already exists.",
        )

    # ------------------------------------------------------------------
    # 2. Upload the optional logo
    # ------------------------------------------------------------------
    new_logo_key: str | None = None

    if firm_logo_key is not None:
        try:
            new_logo_key = await handle_file_update(
                file=firm_logo_key,
                current_key=None,
                prefix=PAYMENT_LOGO,
                max_size=PAYMENT_LOGO_MAX_SIZE,
            )

            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the payment logo.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "firm logo upload failed for name=%s",
                data.name,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload firm logo.",
            ) from exc

    # ------------------------------------------------------------------
    # 3. Prepare the model
    # ------------------------------------------------------------------
    firm = Firm(
        user_id=current_user.id,
        name=data.name,
        registration_number=data.registration_number,
        slug=generate_slug(data.name),
        deals_on=data.deals_on,
        firm_logo_key=new_logo_key,
        disabled=False,
    )

    # ------------------------------------------------------------------
    # 4. Best-effort cleanup for a failed database transaction
    # ------------------------------------------------------------------
    def cleanup_new_logo() -> None:
        if new_logo_key is None:
            return

        try:
            delete_from_r2(new_logo_key)

        except Exception:
            logger.exception(
                "Failed to clean up payment logo in R2: key=%s",
                new_logo_key,
            )

    # ------------------------------------------------------------------
    # 5. Persist the payment method
    # ------------------------------------------------------------------
    try:
        db.add(firm)

        await db.flush()
        await db.refresh(firm)
        await db.commit()

    except IntegrityError as exc:
        await db.rollback()
        cleanup_new_logo()

        logger.warning(
            "firm creation violated a database integrity "
            "constraint: name=%s, provider_code=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"firm '{data.name}' already exists "
                "or conflicts with a database constraint."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        await db.rollback()
        cleanup_new_logo()

        logger.exception(
            "Database error creating firm name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create firm. Please try again.",
        ) from exc

    except Exception as exc:
        await db.rollback()
        cleanup_new_logo()

        logger.exception(
            "Unexpected error creating firm name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create firm. Please try again.",
        ) from exc


    # ------------------------------------------------------------------
    # 7. Return the response
    # ------------------------------------------------------------------
    return {
        "message": f"Firm '{firm.name}' created successfully",
        "firm": FirmRead.model_validate(firm),
    }
  

async def read_single_firm(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmRead:
    """
    Get a single firm by slug, with its full branch list nested
    under `firm.branches`, sorted by `branch_no` ascending.

    Access:
        - owner of the firm
        - any admin
    """
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
            detail=f"Firm '{slug}' id not found",
        )

    #guard_firm_state(firm)
    result =await require_firm_access(db=db,firm_id=firm.id,current_user=current_user)
    fid = firm_id_of(result)

    # ------------------------------------------------------------------
    # Load branches for this firm
    # ------------------------------------------------------------------
    try:
        branches_result = await db.execute(
            select(Branch)
            .where(col(Branch.firm_id) == fid)
            .order_by(col(Branch.branch_no).asc())
        )
        branches = branches_result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load branches for firm_id=%s", fid
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )

    # ------------------------------------------------------------------
    # Count receipts for this firm (across all its branches)
    # ------------------------------------------------------------------
    try:
        receipt_count: int = (
            await db.execute(
                select(func.count())
                .select_from(Receipt)
                .join(Branch, col(Receipt.branch_id) == col(Branch.id))
                .where(col(Branch.firm_id) == fid)
            )
        ).scalar() or 0
    except Exception:
        logger.exception(
            "Failed to count receipts for firm_id=%s", fid
        )
        receipt_count = 0

    # ------------------------------------------------------------------
    # Assemble
    # ------------------------------------------------------------------
    read = FirmRead.model_validate(result)
    read.branches = [ 
        BranchRead.model_validate(b) for b in branches
    ]
    read.branch_count = len(branches)
    read.receipt_count = receipt_count

    return read



async def read_my_firms(
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmListRead:
    guard_user_state(current_user)

    try:
        result = await db.execute(
            select(Firm)
            .where(Firm.user_id == current_user.id)
            .order_by(col(Firm.name))
        )
        firms = result.scalars().all()
        ids = [firm_id_of(f) for f in firms]
        branch_counts, receipt_counts = await counts_by_firm(db, ids)
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
        branch_counts, receipt_counts = await counts_by_firm(db, ids)
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




async def update_firm(
    slug: str,
    data: FirmUpdate,
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
            detail=f"Firm '{slug}'id  not found",
        )

    #guard_firm_state(firm)
    result = await require_firm_access(db=db,firm_id=firm.id,current_user=current_user)

    # if firm.user_id != current_user.id and not current_user.is_admin:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="You can only update your own firm",
    #         headers={"X-Error-Code": "wrong_permission"},
    #     )

    if all(v is None for v in (data.name,  data.registration_number, data.deals_on)):
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
        firm.name = data.name
        firm.slug = generate_slug(data.name)
        updated_fields.append("name")
        logger.info(
            "Firm slug updated: '%s' -> '%s'", old_slug, firm.slug
        )
        
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
    read.branch_count = await count_branches(db, fid)
    read.receipt_count = await count_receipts(db, fid)

    return {
        "message": f"Firm '{firm.name}' updated successfully",
        "firm": read,
    }


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

# i want my update_firm to follow this patter below


async def update_service(
    slug: str,
    data: ServiceUpdate,
    db: AsyncSession,
    service_image: UploadFile | None = None,
) -> dict:
    """
    Update a subscription service.

    - Rejects an empty payload.
    - Updates only fields that actually changed.
    - Uploads a replacement image before touching the DB record.
    - Deletes the new upload after a failed flush only if rollback
      succeeds.
    - Preserves the new upload if commit raises, because its outcome
      may be uncertain.
    - Deletes the old image only after a confirmed successful commit.

    No IntegrityError handling is needed — this endpoint cannot violate
    a unique constraint (name and slug are not editable here).
    """

    # ---------------------------------------------------------
    # 1. Retrieve the existing service
    # ---------------------------------------------------------
    service = await get_service_by_slug(db=db, slug=slug)

    # ==============================================================
    # 2. Reject completely empty update payload
    # ==============================================================
    if all(
        value is None
        for value in (
            data.description,
            data.trial_days,
            service_image,
            data.is_active,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ---------------------------------------------------------
    # 3. Preserve the old image key
    # ---------------------------------------------------------
    old_image_key = service.service_logo_key
    new_image_key: str | None = None

    # ---------------------------------------------------------
    # 4. Upload a replacement image, if supplied
    # ---------------------------------------------------------
    if service_image is not None:
        try:
            new_image_key = await handle_file_update(
                file=service_image,
                current_key=None,
                prefix=SERVICE_IMAGE,
                max_size=SERVICE_IMAGE_MAX_SIZE,
            )

            if not new_image_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the service image.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Service image upload failed: service_id=%s",
                service.id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload service image.",
            ) from exc

    # ==============================================================
    # 5. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    if data.description is not None:
        if data.description != service.description:
            service.description = data.description
            updated_fields.append("description")

    if data.trial_days is not None:
        if data.trial_days != service.trial_days:
            service.trial_days = data.trial_days
            updated_fields.append("trial_days")

    if data.is_active is not None:
        if data.is_active != service.is_active:
            service.is_active = data.is_active
            updated_fields.append("is_active")

    if new_image_key is not None:
        if new_image_key != service.service_logo_key:
            service.service_logo_key = new_image_key
            updated_fields.append("service_logo_key")

    # ==============================================================
    # 6. Reject if nothing actually changed
    # ==============================================================
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ---------------------------------------------------------
    # 7. Flush before attempting commit
    #
    # No IntegrityError branch — this update cannot violate a
    # unique constraint. SQLAlchemyError and Exception cover
    # the realistic failure modes.
    # ---------------------------------------------------------
    try:
        await db.flush()

    except SQLAlchemyError as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after service flush error: "
                "service_id=%s",
                service.id,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "failure: service_id=%s, image_key=%s",
                    service.id,
                    new_image_key,
                )

        logger.exception(
            "Database error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            service.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update service. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    except Exception as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error: "
                "service_id=%s",
                service.id,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "failure: service_id=%s, image_key=%s",
                    service.id,
                    new_image_key,
                )

        logger.exception(
            "Unexpected error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            service.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating service.",
        ) from exc

    # ---------------------------------------------------------
    # 8. Commit the transaction
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # IMPORTANT:
        # Do not delete new_image_key here.
        #
        # A commit exception does not prove the database rejected
        # the transaction. The commit may have succeeded while its
        # acknowledgement was lost.
        #
        # Preserve the uploaded image until the database state can
        # be verified and any orphaned object reconciled.

        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "service_id=%s",
                service.id,
            )

        logger.exception(
            "Service commit failed or has an uncertain outcome. "
            "Preserving replacement image for reconciliation: "
            "service_id=%s, new_image_key=%s",
            service.id,
            new_image_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 9. Commit succeeded: refresh and delete the old image
    # ---------------------------------------------------------
    try:
        await db.refresh(service)
    except Exception:
        # Refresh failure after commit is not fatal — the update
        # is already persisted. Log and continue.
        logger.exception(
            "Failed to refresh service after update: service_id=%s",
            service.id,
        )

    if (
        new_image_key is not None
        and old_image_key is not None
        and old_image_key != new_image_key
    ):
        try:
            delete_from_r2(old_image_key)

        except Exception:
            # The database update has already succeeded.
            # Do not report the update as failed because old-image
            # cleanup failed; log it for later reconciliation.
            logger.exception(
                "Service updated, but the old image could not be "
                "deleted: service_id=%s, old_image_key=%s",
                service.id,
                old_image_key,
            )

    # ---------------------------------------------------------
    # 10. Return the updated service
    # ---------------------------------------------------------
    logger.info(
        "Service updated successfully: service_id=%s, fields=%s",
        service.id,
        ",".join(updated_fields),
    )

    return {
        "message": "Service updated successfully.",
        "service": to_service_read(service),
        }

"""
then i want read_my_firms to returns only firms with branch count
eg abc and sons -> branches 12,abx ltd -> branches 10

iwant read_single_firm to returns all braches associated with a firm slug and receipt counts
eg abc and sons slugs will return Branch no 1,No 12 old rd gra -> 1200 receipts


"""
    

        @router.post(
    "/add/firm",
    response_model=CreateFirmResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a firm (authenticated)",
)
async def create(
    db: DBDep,
    current_user: CurrentUser,
    name: str = Form(...),
    deals_on: Annotated[str | None, Form(max_length=1200)] = None,
    registration_number: Annotated[str | None, Form(max_length=1200)] = None,
    firm_logo_key: UploadFile | None = File(None),
    ):
    data = FirmCreate(name=name,deals_on=deals_on)
    
    return await create_firm(
        data=data, db=db, current_user=current_user
    )
      #also this my crate_firm routes a
   
