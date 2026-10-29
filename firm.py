"""Generic media URL resolution for storage keys."""

from api.core.storage import get_public_url  # your existing R2 helper


def resolve_logo_url(key: str | None) -> str | None:
    """
    Turn a storage key into a public URL.

    Returns None if the key is None or empty, so callers never
    have to branch on truthiness.

    Example:
        resolve_logo_url("firms/42/abc-business-9f8e7d.png")
        # -> "https://cdn.yourapp.com/firms/42/abc-business-9f8e7d.png"

        resolve_logo_url(None)
        # -> None
    """
    if not key:
        return None
    return get_public_url(key)




"""Firm & Branch schemas."""

import re
from decimal import Decimal
from datetime import datetime
from typing import Literal, Self

from pydantic_core.core_schema import FieldValidationInfo

from pydantic import (
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
    normalize_payment_option,
)


# =============================================================================
# FIRM — REQUEST
# =============================================================================

class FirmCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=200)
    registration_number: str | None = Field(
        default=None, min_length=2, max_length=50,
    )
    deals_on: str = Field(..., min_length=10, max_length=2000)

    @field_validator("name", mode="before")
    @classmethod
    def validate_firm_name(cls, v: str) -> str:
        return normalize_firm_name(v)

    @field_validator("deals_on", mode="before")
    @classmethod
    def validate_deal_on_fields(
        cls, v: str, info: FieldValidationInfo,
    ) -> str:
        raw_name = info.field_name or "field"
        display_name = raw_name.replace("_", " ").capitalize()
        return normalize_user_message(v, field_name=display_name)


class FirmUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=2, max_length=200)
    registration_number: str | None = Field(
        default=None, min_length=2, max_length=50,
    )
    deals_on: str | None = Field(None, min_length=10, max_length=2000)

    @field_validator("name", mode="before")
    @classmethod
    def validate_firmname(cls, v: str | None) -> str | None:
        return normalize_firm_name(v) if v else None

    @field_validator("deals_on", mode="before")
    @classmethod
    def validate_deal_on_fields(
        cls, v: str | None, info: FieldValidationInfo,
    ) -> str | None:
        if v is None:
            return None
        raw_name = info.field_name or "field"
        display_name = raw_name.replace("_", " ").capitalize()
        return normalize_user_message(v, field_name=display_name)


class FirmAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=200)


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
# BRANCH — READ
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

    receipt_count: int = 0


class BranchListRead(BaseModel):
    firm_id: int
    firm_name: str
    total: int
    branches: list[BranchRead]


# =============================================================================
# FIRM — READ
#
# `firm_logo_url` is exposed; `firm_logo_key` is internal and never
# leaves the backend. The builder populates the URL from the key.
# =============================================================================

class FirmListItem(BaseModel):
    """
    Firm summary row for list endpoints.

    No `firm_logo_key` — internal. No `receipt_count` — not computed
    in list views.
    """
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

    branch_count: int = 0
    firm_logo_url: str | None = None


class FirmListRead(BaseModel):
    total: int
    firms: list[FirmListItem]


class FirmRead(BaseModel):
    """
    Full firm detail.

    No `firm_logo_key` — internal. `firm_logo_url` is populated by
    the builder. `branches[]` is populated only by read_single_firm.
    """
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

    branch_count: int = 0
    receipt_count: int = 0
    branches: list[BranchRead] = []
    firm_logo_url: str | None = None


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


class DeleteBranchResponse(BaseModel):
    message: str
    branch_no: int




# =============================================================================
# BUILDERS — single source of truth for read-shape assembly
# =============================================================================

def to_branch_read(
    branch: Branch,
    *,
    receipt_count: int = 0,
) -> BranchRead:
    """Assemble a BranchRead with its receipt counter."""
    read = BranchRead.model_validate(branch)
    read.receipt_count = receipt_count
    return read


def to_branch_list(
    firm: Firm,
    branches: Sequence[Branch],
    receipt_counts: Mapping[int, int],
) -> BranchListRead:
    """Assemble the branch-list envelope for a firm."""
    items = [
        to_branch_read(b, receipt_count=receipt_counts.get(b.id, 0))
        for b in branches
    ]
    return BranchListRead(
        firm_id=firm.id,
        firm_name=firm.name,
        total=len(items),
        branches=items,
    )


def to_firm_list_item(
    firm: Firm,
    *,
    branch_count: int = 0,
) -> FirmListItem:
    """Assemble a FirmListItem (branch count only, logo URL resolved)."""
    item = FirmListItem.model_validate(firm)
    item.branch_count = branch_count
    item.firm_logo_url = resolve_logo_url(firm.firm_logo_key)
    return item


def to_firm_list(
    firms: Sequence[Firm],
    branch_counts: Mapping[int, int],
    *,
    total: int | None = None,
) -> FirmListRead:
    """Assemble the firms-list envelope."""
    items = [
        to_firm_list_item(f, branch_count=branch_counts.get(f.id, 0))
        for f in firms
    ]
    return FirmListRead(
        total=total if total is not None else len(items),
        firms=items,
    )


def to_firm_detail(
    firm: Firm,
    branches: Sequence[Branch] = (),
    receipt_counts: Mapping[int, int] | None = None,
) -> FirmRead:
    """
    Assemble the full FirmRead.

    `branches` and `receipt_counts` are optional so that
    create/update/disable/enable can reuse this builder without
    loading branches.
    """
    receipt_counts = receipt_counts or {}

    branch_reads = [
        to_branch_read(b, receipt_count=receipt_counts.get(b.id, 0))
        for b in branches
    ]

    read = FirmRead.model_validate(firm)
    read.branches = branch_reads
    read.branch_count = len(branch_reads)
    read.receipt_count = sum(br.receipt_count for br in branch_reads)
    read.firm_logo_url = resolve_logo_url(firm.firm_logo_key)
    return read




async def read_single_firm(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmRead:
    """
    Full firm detail: firm fields, resolved logo URL, nested branches
    (each with its own receipt_count), and aggregate counters.
    """
    guard_user_state(current_user)

    # 1. Resolve the firm
    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db=db, firm_id=fid, current_user=current_user,
    )

    # 2. Load branches
    try:
        branches_result = await db.execute(
            select(Branch)
            .where(col(Branch.firm_id) == fid)
            .order_by(col(Branch.branch_no).asc())
        )
        branches = list(branches_result.scalars().all())
    except Exception:
        logger.exception(
            "Failed to load branches for firm_id=%s", fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )

    # 3. Batch receipt counts per branch
    branch_ids = [branch_id_of(b) for b in branches]
    receipt_counts = await receipt_counts_by_branch(db, branch_ids)

    # 4. Assemble via builder
    return to_firm_detail(firm, branches, receipt_counts)



async def read_my_firms(
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmListRead:
    """
    List current user's firms with branch_count and firm_logo_url.

    No receipt counts — the list contract intentionally omits them.
    """
    guard_user_state(current_user)

    try:
        result = await db.execute(
            select(Firm)
            .where(Firm.user_id == current_user.id)
            .order_by(col(Firm.name))
        )
        firms = list(result.scalars().all())
        ids = [firm_id_of(f) for f in firms]
        branch_counts, _ = await counts_by_firm(db, ids)
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s", current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load your firms. Please try again.",
        )

    return to_firm_list(firms, branch_counts)





async def read_all_firms(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> FirmListRead:
    """Admin-only via route-level AdminUser dependency."""
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
        firms = list(result.scalars().all())
        ids = [firm_id_of(f) for f in firms]
        branch_counts, _ = await counts_by_firm(db, ids)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list firms")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firms. Please try again.",
        )

    return to_firm_list(firms, branch_counts, total=total)





"""Firm & branch routes."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    UploadFile,
    status,
)

from api.deps import DBDep, CurrentUser
from api.schemas.firm import (
    BranchCreate,
    BranchListRead,
    BranchUpdate,
    CreateBranchResponse,
    CreateFirmResponse,
    DeleteBranchResponse,
    DeleteFirmResponse,
    FirmAction,
    FirmAdminActionResponse,
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
    UpdateBranchResponse,
    UpdateFirmResponse,
)
from api.services.firm import (
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


router = APIRouter(prefix="/firms", tags=["Firms"])


# =============================================================================
# FIRM — CREATE
# =============================================================================

@router.post(
    "/add",
    response_model=CreateFirmResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a firm (authenticated)",
)
async def create(
    db: DBDep,
    current_user: CurrentUser,
    name: Annotated[str, Form(min_length=2, max_length=200)],
    deals_on: Annotated[str, Form(min_length=10, max_length=2000)],
    registration_number: Annotated[
        str | None, Form(max_length=50)
    ] = None,
    firm_logo_key: UploadFile | None = File(None),
):
    data = FirmCreate(
        name=name,
        deals_on=deals_on,
        registration_number=registration_number,
    )
    return await create_firm(
        data=data,
        db=db,
        current_user=current_user,
        firm_logo_key=firm_logo_key,
    )


# =============================================================================
# FIRM — READ (MY FIRMS)
#
# Returns FirmListRead → each firm carries branch_count only.
# Example response:
#   {
#     "total": 2,
#     "firms": [
#       { "name": "abc business", "branch_count": 5, ... },
#       { "name": "xyz ltd",      "branch_count": 3, ... }
#     ]
#   }
# =============================================================================

@router.get(
    "/mine",
    response_model=FirmListRead,
    summary="List my firms (branch count only)",
)
async def my_firms(
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_my_firms(db=db, current_user=current_user)


# =============================================================================
# FIRM — READ (ALL FIRMS, ADMIN)
# =============================================================================

@router.get(
    "/all",
    response_model=FirmListRead,
    summary="List all firms (admin, branch count only)",
)
async def all_firms(
    db: DBDep,
    current_user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    return await read_all_firms(db=db, skip=skip, limit=limit)


# =============================================================================
# FIRM — READ (SINGLE)
#
# Returns FirmRead → nested branches with per-branch receipt_count
# plus aggregate branch_count / receipt_count.
# =============================================================================

@router.get(
    "/{slug}",
    response_model=FirmRead,
    summary="Get a single firm with its branches",
)
async def single_firm(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_single_firm(
        slug=slug, db=db, current_user=current_user,
    )


# =============================================================================
# FIRM — UPDATE
# =============================================================================

@router.patch(
    "/{slug}",
    response_model=UpdateFirmResponse,
    summary="Update a firm (authenticated)",
)
async def update(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
    name: Annotated[
        str | None, Form(min_length=2, max_length=200)
    ] = None,
    deals_on: Annotated[
        str | None, Form(min_length=10, max_length=2000)
    ] = None,
    registration_number: Annotated[
        str | None, Form(max_length=50)
    ] = None,
    firm_logo_key: UploadFile | None = File(None),
):
    data = FirmUpdate(
        name=name,
        deals_on=deals_on,
        registration_number=registration_number,
    )
    return await update_firm(
        slug=slug,
        data=data,
        db=db,
        current_user=current_user,
        firm_logo_key=firm_logo_key,
    )


# =============================================================================
# FIRM — DELETE
# =============================================================================

@router.delete(
    "/{slug}",
    response_model=DeleteFirmResponse,
    summary="Delete a firm and its branches (authenticated)",
)
async def remove(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await delete_firm(
        slug=slug, db=db, current_user=current_user,
    )


# =============================================================================
# FIRM — ADMIN: DISABLE / ENABLE
# =============================================================================

@router.post(
    "/disable",
    response_model=FirmAdminActionResponse,
    summary="Disable a firm (admin)",
)
async def disable(
    data: FirmAction,
    db: DBDep,
    current_user: CurrentUser,
):
    return await disable_firm(
        data=data, db=db, current_user=current_user,
    )


@router.post(
    "/enable",
    response_model=FirmAdminActionResponse,
    summary="Enable a firm (admin)",
)
async def enable(
    data: FirmAction,
    db: DBDep,
    current_user: CurrentUser,
):
    return await enable_firm(
        data=data, db=db, current_user=current_user,
    )


# =============================================================================
# BRANCH — CREATE
# =============================================================================

@router.post(
    "/{firm_slug}/branches",
    response_model=CreateBranchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a branch to a firm",
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


# =============================================================================
# BRANCH — READ
# =============================================================================

@router.get(
    "/{firm_slug}/branches",
    response_model=BranchListRead,
    summary="List branches for a firm",
)
async def list_branches(
    firm_slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_branches_for_firm(
        firm_slug=firm_slug, db=db, current_user=current_user,
    )


# =============================================================================
# BRANCH — UPDATE
# =============================================================================

@router.patch(
    "/branches/{branch_id}",
    response_model=UpdateBranchResponse,
    summary="Update a branch",
)
async def update_one_branch(
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


# =============================================================================
# BRANCH — DELETE
# =============================================================================

@router.delete(
    "/branches/{branch_id}",
    response_model=DeleteBranchResponse,
    summary="Delete a branch (refused if it has receipts)",
)
async def remove_branch(
    branch_id: int,
    db: DBDep,
    current_user: CurrentUser,
):
    return await delete_branch(
        branch_id=branch_id, db=db, current_user=current_user,
    )



async def create_firm(
    data: FirmCreate,
    db: AsyncSession,
    firm_logo_key: UploadFile | None,
    current_user: ReadUser,
) -> dict:
    """
    Create a firm. Caller must be verified and active.

    - Firm names are globally unique (checked explicitly, then
      enforced by the DB constraint as a second line of defense).
    - The logo is optional and stored in Cloudflare R2.
    - The logo is uploaded before the DB row is written.
    - The new logo is deleted only after a *confirmed* rollback
      when the flush or commit failed.
    - If commit raises (uncertain outcome), the logo is preserved
      for reconciliation.
    - No old logo to delete on create — nothing existed before.
    """

    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 1. Sanity: caller must have an id (defensive, mirrors firm_id_of)
    # ------------------------------------------------------------------
    if current_user.id is None:
        logger.error("Current user has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create firm. Please try again.",
        )
    owner_id = current_user.id

    # ------------------------------------------------------------------
    # 2. Pre-check: firm name must be globally unique
    #
    # This is a fast-fail for the common case. It is NOT a substitute
    # for the DB unique constraint — the TOCTOU window between this
    # check and the commit is closed by the IntegrityError branch
    # in step 7.
    # ------------------------------------------------------------------
    try:
        existing = await get_firm_by_name(db, data.name)

    except HTTPException:
        # get_firm_by_name already raised a clean 500 on SQLAlchemyError
        raise

    except Exception as exc:
        logger.exception(
            "Database error checking firm name=%s", data.name,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create firm. Please try again.",
        ) from exc

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Firm with name '{data.name}' already exists.",
        )

    # ------------------------------------------------------------------
    # 3. Pre-check: registration_number must be unique *if provided*
    #
    # Mirrors the update_firm behavior: registration_number is a
    # legal identity. Null is fine, but non-null must not collide.
    # ------------------------------------------------------------------
    if data.registration_number is not None:
        try:
            existing_reg = await get_firm_by_registration_number(
                db, data.registration_number,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "Database error checking firm registration_number=%s",
                data.registration_number,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create firm. Please try again.",
            ) from exc

        if existing_reg is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Registration number "
                    f"'{data.registration_number}' is already registered."
                ),
            )

    # ------------------------------------------------------------------
    # 4. Upload the optional logo
    #
    # Nothing in the DB has changed yet. On upload failure we simply
    # abort — there is nothing to roll back.
    # ------------------------------------------------------------------
    new_logo_key: str | None = None

    if firm_logo_key is not None:
        try:
            new_logo_key = await handle_file_update(
                file=firm_logo_key,
                current_key=None,
                prefix=FIRM_LOGO,
                max_size=FIRM_LOGO_MAX_SIZE,
            )

            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the firm logo.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Firm logo upload failed for name=%s", data.name,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload firm logo.",
            ) from exc

    # ------------------------------------------------------------------
    # 5. Prepare the model
    # ------------------------------------------------------------------
    firm = Firm(
        user_id=owner_id,
        name=data.name,
        registration_number=data.registration_number,
        slug=generate_slug(data.name),
        deals_on=data.deals_on,
        firm_logo_key=new_logo_key,
        disabled=False,
    )

    db.add(firm)

    # ------------------------------------------------------------------
    # 6. Flush before attempting commit
    #
    # IntegrityError at flush time is the *expected* loser in a
    # concurrent create race — the pre-check in step 2 passed but
    # another request committed first. We clean up the logo only
    # if rollback succeeds.
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after firm IntegrityError: name=%s",
                data.name,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up logo after IntegrityError: "
                    "name=%s, logo_key=%s",
                    data.name, new_logo_key,
                )

        logger.warning(
            "Firm creation violated a database integrity constraint: "
            "name=%s, registration_number=%s, rollback_succeeded=%s",
            data.name, data.registration_number, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Firm '{data.name}' already exists "
                f"or conflicts with a database constraint."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after firm flush error: name=%s",
                data.name,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up logo after flush failure: "
                    "name=%s, logo_key=%s",
                    data.name, new_logo_key,
                )

        logger.exception(
            "Database error while flushing new firm: "
            "name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create firm. "
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
                "Rollback failed after unexpected flush error: name=%s",
                data.name,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up logo after flush failure: "
                    "name=%s, logo_key=%s",
                    data.name, new_logo_key,
                )

        logger.exception(
            "Unexpected error while flushing new firm: "
            "name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating firm.",
        ) from exc

    # ------------------------------------------------------------------
    # 7. Commit the transaction
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # IMPORTANT:
        # Do not delete new_logo_key here.
        #
        # A commit exception does not prove the DB rejected the
        # transaction. The commit may have succeeded while its
        # acknowledgement was lost. Preserve the uploaded logo
        # until the DB state can be verified.
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: name=%s",
                data.name,
            )

        logger.exception(
            "Firm commit failed or has an uncertain outcome. "
            "Preserving logo for reconciliation: "
            "name=%s, new_logo_key=%s",
            data.name, new_logo_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The firm creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 8. Commit succeeded: refresh (best-effort)
    # ------------------------------------------------------------------
    try:
        await db.refresh(firm)
    except Exception:
        # Refresh failure after commit is not fatal — the row is
        # persisted. Log and continue.
        logger.exception(
            "Failed to refresh firm after create: name=%s", data.name,
        )

    fid = firm_id_of(firm)

    logger.info(
        "Firm created successfully: firm_id=%s, name=%s, owner_id=%s",
        fid, firm.name, owner_id,
    )

    # ------------------------------------------------------------------
    # 9. Return the response (counters are 0 for a fresh firm, but
    #    call the helpers anyway so the shape matches read_single_firm)
    # ------------------------------------------------------------------
    read = FirmRead.model_validate(firm)
    read.branch_count = 0
    read.receipt_count = 0

    return {
        "message": f"Firm '{firm.name}' created successfully",
        "firm": read,
        }




async def receipt_counts_by_branch(
    db: AsyncSession,
    branch_ids: list[int],
) -> dict[int, int]:
    """Batch receipt counts for many branches in one query."""
    if not branch_ids:
        return {}

    try:
        result = await db.execute(
            select(Receipt.branch_id, func.count())
            .where(col(Receipt.branch_id).in_(branch_ids))
            .group_by(col(Receipt.branch_id))
        )
        return {row[0]: int(row[1]) for row in result.all()}
    except Exception:
        logger.exception(
            "Failed to batch-count receipts for %s branches",
            len(branch_ids),
        )
        return {}





async def update_firm(
    slug: str,
    data: FirmUpdate,
    db: AsyncSession,
    current_user: ReadUser,
    firm_logo_key: UploadFile | None = None,
) -> dict:
    """
    Update a firm.

    - Rejects an empty payload.
    - Updates only fields that actually changed.
    - Uploads a replacement logo before touching the DB record.
    - Deletes the new upload after a failed flush only if rollback
      succeeds.
    - Preserves the new upload if commit raises, because its outcome
      may be uncertain.
    - Deletes the old logo only after a confirmed successful commit.

    Uniqueness of `name` and `registration_number` is enforced by
    explicit pre-checks below, so no IntegrityError branch is needed
    for the common path. We still catch SQLAlchemyError/Exception for
    infrastructure failures.
    """

    guard_user_state(current_user)

    # ---------------------------------------------------------
    # 1. Retrieve the existing firm
    # ---------------------------------------------------------
    firm = await get_firm_by_slug(db=db, slug=slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    fid = firm_id_of(firm)
    await require_firm_access(db=db, firm_id=fid, current_user=current_user)

    # ==============================================================
    # 2. Reject completely empty update payload
    # ==============================================================
    if all(
        value is None
        for value in (
            data.name,
            data.registration_number,
            data.deals_on,
            firm_logo_key,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ---------------------------------------------------------
    # 3. Preserve the old logo key
    # ---------------------------------------------------------
    old_logo_key = firm.firm_logo_key
    new_logo_key: str | None = None

    # ---------------------------------------------------------
    # 4. Upload a replacement logo, if supplied
    # ---------------------------------------------------------
    if firm_logo_key is not None:
        try:
            new_logo_key = await handle_file_update(
                file=firm_logo_key,
                current_key=None,
                prefix=FIRM_LOGO,
                max_size=FIRM_LOGO_MAX_SIZE,
            )

            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the firm logo.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Firm logo upload failed: firm_id=%s", fid,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload firm logo.",
            ) from exc

    # ==============================================================
    # 5. Update only fields that actually changed
    #
    #    Name change requires a uniqueness check (name is unique
    #    across all firms) and a slug regeneration.
    #    registration_number change requires a uniqueness check.
    # ==============================================================
    updated_fields: list[str] = []

    # --- name ---
    if data.name is not None and data.name != firm.name:
        existing = await get_firm_by_name(db, data.name)
        if existing is not None and firm_id_of(existing) != fid:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Firm name '{data.name}' is already taken",
            )

        old_slug = firm.slug
        firm.name = data.name
        firm.slug = generate_slug(data.name)
        updated_fields.append("name")
        logger.info(
            "Firm slug updated: '%s' -> '%s'", old_slug, firm.slug,
        )

    # --- registration_number ---
    # Clearing is intentionally disallowed: it's a legal identity.
    if (
        data.registration_number is not None
        and data.registration_number != firm.registration_number
    ):
        existing_reg = await get_firm_by_registration_number(
            db, data.registration_number,
        )
        if (
            existing_reg is not None
            and firm_id_of(existing_reg) != fid
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Registration number "
                    f"'{data.registration_number}' is already registered"
                ),
            )

        firm.registration_number = data.registration_number
        updated_fields.append("registration_number")

    # --- deals_on ---
    if data.deals_on is not None and data.deals_on != firm.deals_on:
        firm.deals_on = data.deals_on
        updated_fields.append("deals_on")

    # --- logo ---
    if new_logo_key is not None:
        if new_logo_key != firm.firm_logo_key:
            firm.firm_logo_key = new_logo_key
            updated_fields.append("firm_logo_key")

    # ==============================================================
    # 6. Reject if nothing actually changed
    # ==============================================================
    if not updated_fields:
        # Clean up an uploaded-but-unused logo so we don't leak it.
        if new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up unused logo after no-op "
                    "update: firm_id=%s, logo_key=%s",
                    fid, new_logo_key,
                )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ---------------------------------------------------------
    # 7. Flush before attempting commit
    #
    # Uniqueness was already enforced by the explicit queries in
    # step 5, so IntegrityError is not expected here. We handle
    # SQLAlchemyError and generic Exception.
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
                "Rollback failed after firm flush error: firm_id=%s",
                fid,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush "
                    "failure: firm_id=%s, logo_key=%s",
                    fid, new_logo_key,
                )

        logger.exception(
            "Database error while flushing firm update: "
            "firm_id=%s, rollback_succeeded=%s",
            fid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update firm. "
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
                "Rollback failed after unexpected flush error: firm_id=%s",
                fid,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush "
                    "failure: firm_id=%s, logo_key=%s",
                    fid, new_logo_key,
                )

        logger.exception(
            "Unexpected error while flushing firm update: "
            "firm_id=%s, rollback_succeeded=%s",
            fid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating firm.",
        ) from exc

    # ---------------------------------------------------------
    # 8. Commit the transaction
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # IMPORTANT:
        # Do not delete new_logo_key here.
        #
        # A commit exception does not prove the DB rejected the
        # transaction. The commit may have succeeded while its
        # acknowledgement was lost. Preserve the uploaded logo
        # until the DB state can be verified.
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: firm_id=%s",
                fid,
            )

        logger.exception(
            "Firm commit failed or has an uncertain outcome. "
            "Preserving replacement logo for reconciliation: "
            "firm_id=%s, new_logo_key=%s",
            fid, new_logo_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The firm update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 9. Commit succeeded: refresh and delete the old logo
    # ---------------------------------------------------------
    try:
        await db.refresh(firm)
    except Exception:
        # Refresh failure after commit is not fatal — the update
        # is already persisted. Log and continue.
        logger.exception(
            "Failed to refresh firm after update: firm_id=%s", fid,
        )

    if (
        new_logo_key is not None
        and old_logo_key is not None
        and old_logo_key != new_logo_key
    ):
        try:
            delete_from_r2(old_logo_key)
        except Exception:
            # The DB update has already succeeded. Do not report
            # the update as failed because old-logo cleanup failed.
            logger.exception(
                "Firm updated, but the old logo could not be deleted: "
                "firm_id=%s, old_logo_key=%s",
                fid, old_logo_key,
            )

    # ---------------------------------------------------------
    # 10. Return the updated firm (with counters, same shape as read)
    # ---------------------------------------------------------
    logger.info(
        "Firm updated successfully: firm_id=%s, fields=%s",
        fid, ",".join(updated_fields),
    )

    read = FirmRead.model_validate(firm)
    read.branch_count = await count_branches(db, fid)
    read.receipt_count = await count_receipts(db, fid)

    return {
        "message": f"Firm '{firm.name}' updated successfully",
        "firm": read,
            }





