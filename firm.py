



async def delete_branch(
    branch_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a branch.

    - Refused if the branch has issued receipts (FK is RESTRICT).
    - The receipt count is checked explicitly for a friendly error,
      but the DB FK remains the authoritative guard against a
      concurrent insert between the count and the delete. That
      race shows up as an IntegrityError at flush time.
    """

    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 1. Resolve the branch
    # ------------------------------------------------------------------
    branch = await get_branch_by_id(db, branch_id)
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Branch id={branch_id} not found",
        )

    bid = branch_id_of(branch)
    fid = branch.firm_id

    # ------------------------------------------------------------------
    # 2. Enforce access via the firm
    # ------------------------------------------------------------------
    firm = await db.get(Firm, fid)
    if firm is None:
        logger.error(
            "Branch %s references missing firm_id=%s", bid, fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Firm not found for this branch",
        )

    firm = await require_firm_access(
        db=db, firm_id=fid, current_user=current_user,
    )

    # ------------------------------------------------------------------
    # 3. Refuse if receipts exist (friendly pre-check)
    # ------------------------------------------------------------------
    receipt_count = await count_receipts_for_branch(db, bid)
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

    # ------------------------------------------------------------------
    # 4. Stage the delete and flush
    #
    # IntegrityError at flush time = a receipt was inserted
    # concurrently after step 3. The DB RESTRICT FK rejects the
    # delete, which is the correct behavior.
    # ------------------------------------------------------------------
    try:
        await db.delete(branch)
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch IntegrityError: "
                "branch_id=%s", bid,
            )

        logger.warning(
            "Branch delete blocked by FK constraint: branch_id=%s, "
            "rollback_succeeded=%s",
            bid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch {branch_no} now has receipts and cannot "
                f"be deleted. Refresh and try again."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch delete flush error: "
                "branch_id=%s", bid,
            )

        logger.exception(
            "Database error while flushing branch delete: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to delete branch. "
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
                "Rollback failed after unexpected delete flush error: "
                "branch_id=%s", bid,
            )

        logger.exception(
            "Unexpected error while flushing branch delete: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting branch.",
        ) from exc

    # ------------------------------------------------------------------
    # 5. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "branch_id=%s", bid,
            )

        logger.exception(
            "Branch delete commit failed or has an uncertain outcome: "
            "branch_id=%s", bid,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The branch deletion could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    logger.info(
        "Branch %s (firm '%s') deleted by user_id=%s",
        branch_no, firm.name, current_user.id,
    )

    return {
        "message": f"Branch {branch_no} deleted successfully",
        "branch_no": branch_no,
    }




async def get_branch_by_firm_and_no(
    db: AsyncSession,
    firm_id: int,
    branch_no: int,
    exclude_branch_id: int | None = None,
) -> Branch | None:
    """
    Fetch a branch by (firm_id, branch_no).

    `exclude_branch_id` is used during updates so we don't collide
    with ourselves when the caller resubmits the same branch_no.
    """
    try:
        stmt = select(Branch).where(
            col(Branch.firm_id) == firm_id,
            col(Branch.branch_no) == branch_no,
        )
        if exclude_branch_id is not None:
            stmt = stmt.where(col(Branch.id) != exclude_branch_id)

        result = await db.execute(stmt)
        return result.scalars().first()

    except Exception as exc:
        logger.exception(
            "Failed to fetch branch firm_id=%s branch_no=%s",
            firm_id, branch_no,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load branch. Please try again.",
        ) from exc


async def count_receipts_for_branch(
    db: AsyncSession,
    branch_id: int,
) -> int:
    """Count receipts issued against a single branch."""
    try:
        return (
            await db.execute(
                select(func.count())
                .select_from(Receipt)
                .where(col(Receipt.branch_id) == branch_id)
            )
        ).scalar() or 0
    except Exception:
        logger.exception(
            "Failed to count receipts for branch_id=%s", branch_id,
        )
        return 0

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









