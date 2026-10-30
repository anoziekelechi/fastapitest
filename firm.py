async def get_branch_by_slug(
    db: AsyncSession,
    firm_id: int,
    slug: str,
) -> Branch | None:
    """
    Fetch a branch by (firm_id, slug).

    Scoped to a firm so identical slugs can coexist across firms
    (assuming the compound unique constraint above). If you keep
    global slug uniqueness, drop `firm_id` from the WHERE clause.
    """
    try:
        result = await db.execute(
            select(Branch).where(
                col(Branch.firm_id) == firm_id,
                col(Branch.slug) == slug,
            )
        )
        return result.scalars().first()
    except Exception as exc:
        logger.exception(
            "Failed to fetch branch firm_id=%s slug=%s", firm_id, slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load branch. Please try again.",
        ) from exc



async def create_branch(
    firm_slug: str,
    data: BranchCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    guard_user_state(current_user)

    # 1. Resolve firm + access
    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )
    guard_firm_state(firm)
    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db=db, firm_id=fid, current_user=current_user,
    )

    # 2. Pre-check (firm_id, branch_no)
    existing = await get_branch_by_firm_and_no(
        db=db, firm_id=fid, branch_no=data.branch_no,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch {data.branch_no} already exists "
                f"for '{firm.name}'"
            ),
        )

    # 3. Generate a slug unique within this firm
    #
    # Prefer the address for readability; fall back to branch_no.
    # `generate_unique_branch_slug` normalizes via generate_slug().
    slug_base = data.address or f"branch-{data.branch_no}"
    branch_slug = await generate_unique_branch_slug(
        db, firm_id=fid, base=slug_base,
    )

    # 4. Prepare the model
    branch = Branch(
        firm_id=fid,
        branch_no=data.branch_no,
        slug=branch_slug,
        address=data.address,
        phone_number=data.phone_number,
    )

    db.add(branch)

    # 5. flush → commit (unchanged from your current logic)
    try:
        await db.flush()
    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch IntegrityError: "
                "firm_id=%s, branch_no=%s", fid, data.branch_no,
            )

        logger.warning(
            "Branch creation violated a database integrity constraint: "
            "firm_id=%s, branch_no=%s, rollback_succeeded=%s",
            fid, data.branch_no, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch {data.branch_no} already exists "
                f"for '{firm.name}'"
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch flush error: firm_id=%s",
                fid,
            )
        logger.exception(
            "Database error while flushing new branch: "
            "firm_id=%s, rollback_succeeded=%s",
            fid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create branch. "
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
        logger.exception(
            "Unexpected error while flushing new branch: "
            "firm_id=%s, rollback_succeeded=%s",
            fid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating branch.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: firm_id=%s",
                fid,
            )
        logger.exception(
            "Branch commit failed or has an uncertain outcome: "
            "firm_id=%s, branch_no=%s", fid, data.branch_no,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The branch creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(branch)
    except Exception:
        logger.exception(
            "Failed to refresh branch after create: firm_id=%s", fid,
        )

    logger.info(
        "Branch %s (slug=%s) created for firm '%s' by user_id=%s",
        branch.branch_no, branch.slug, firm.name, current_user.id,
    )

    return {
        "message": (
            f"Branch {branch.branch_no} created successfully "
            f"for '{firm.name}'"
        ),
        "branch": to_branch_read(branch, receipt_count=0),
        }




class BranchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_no: str = Field(..., min_length=1, max_length=50)
    address: str = Field(..., min_length=5, max_length=500)
    phone_number: str
    # validators unchanged




class BranchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Do NOT expose `slug` here — slugs are server-managed. If you
    # ever want user-editable slugs, add it back with a validator
    # that normalizes via generate_slug and checks uniqueness.
    branch_no: str | None = Field(None, min_length=1, max_length=50)
    address: str | None = Field(None, min_length=5, max_length=500)
    phone_number: str | None = None




class BranchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    slug: str | None = None
    branch_no: str
    address: str
    phone_number: str
    created_at: datetime
    updated_at: datetime
    receipt_count: int = 0




async def update_branch(
    firm_slug: str,
    branch_slug: str,
    data: BranchUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update a branch identified by (firm_slug, branch_slug).

    - Rejects empty payloads and no-ops.
    - Slug is immutable here — regenerate only via a dedicated
      admin action if you ever need to rename branches.
    """
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 1. Resolve firm + access
    # ------------------------------------------------------------------
    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db=db, firm_id=fid, current_user=current_user,
    )

    # ------------------------------------------------------------------
    # 2. Resolve branch within this firm
    # ------------------------------------------------------------------
    branch = await get_branch_by_slug(
        db, firm_id=fid, slug=branch_slug,
    )
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Branch '{branch_slug}' not found for '{firm.name}'"
            ),
        )

    bid = branch_id_of(branch)

    # ==============================================================
    # 3. Reject empty payload
    # ==============================================================
    if all(
        v is None
        for v in (data.branch_no, data.address, data.phone_number)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ==============================================================
    # 4. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    # --- branch_no ---
    if data.branch_no is not None and data.branch_no != branch.branch_no:
        existing = await get_branch_by_firm_and_no(
            db=db,
            firm_id=fid,
            branch_no=data.branch_no,
            exclude_branch_id=bid,
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Branch {data.branch_no} already exists "
                    f"for '{firm.name}'"
                ),
            )
        branch.branch_no = data.branch_no
        updated_fields.append("branch_no")

    # --- address ---
    if data.address is not None and data.address != branch.address:
        branch.address = data.address
        updated_fields.append("address")

    # --- phone_number ---
    if (
        data.phone_number is not None
        and data.phone_number != branch.phone_number
    ):
        branch.phone_number = data.phone_number
        updated_fields.append("phone_number")

    # ==============================================================
    # 5. Reject no-op
    # ==============================================================
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ---------------------------------------------------------
    # 6. Flush — IntegrityError here means a concurrent
    #    branch_no insert beat us to it
    # ---------------------------------------------------------
    try:
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
            "Branch update violated a database integrity constraint: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch {data.branch_no} already exists "
                f"for '{firm.name}'"
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch flush error: "
                "branch_id=%s", bid,
            )
        logger.exception(
            "Database error while flushing branch update: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update branch. "
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
                "branch_id=%s", bid,
            )
        logger.exception(
            "Unexpected error while flushing branch update: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating branch.",
        ) from exc

    # ---------------------------------------------------------
    # 7. Commit
    # ---------------------------------------------------------
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
            "Branch commit failed or has an uncertain outcome: "
            "branch_id=%s", bid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The branch update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 8. Refresh + return
    # ---------------------------------------------------------
    try:
        await db.refresh(branch)
    except Exception:
        logger.exception(
            "Failed to refresh branch after update: branch_id=%s", bid,
        )

    logger.info(
        "Branch updated successfully: branch_id=%s, fields=%s",
        bid, ",".join(updated_fields),
    )

    receipt_count = await count_receipts_for_branch(db, bid)

    return {
        "message": f"Branch '{branch.slug}' updated successfully",
        "branch": to_branch_read(branch, receipt_count=receipt_count),
        }




async def delete_branch(
    firm_slug: str,
    branch_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a branch identified by (firm_slug, branch_slug).

    Refused if the branch has receipts (FK RESTRICT). The
    pre-check backs the friendly message; the FK guards the
    concurrent-insert race.
    """
    guard_user_state(current_user)

    # 1. Resolve firm + access
    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db=db, firm_id=fid, current_user=current_user,
    )

    # 2. Resolve branch within firm
    branch = await get_branch_by_slug(
        db, firm_id=fid, slug=branch_slug,
    )
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Branch '{branch_slug}' not found for '{firm.name}'"
            ),
        )

    bid = branch_id_of(branch)

    # 3. Refuse if receipts exist
    receipt_count = await count_receipts_for_branch(db, bid)
    if receipt_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch '{branch.slug}' has {receipt_count} "
                f"{'receipt' if receipt_count == 1 else 'receipts'}. "
                f"Delete or transfer them before removing the branch."
            ),
        )

    deleted_slug = branch.slug

    # 4. Stage + flush
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
                f"Branch '{deleted_slug}' now has receipts and "
                f"cannot be deleted. Refresh and try again."
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

    # 5. Commit
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
        "Branch '%s' (firm '%s') deleted by user_id=%s",
        deleted_slug, firm.name, current_user.id,
    )

    return {
        "message": f"Branch '{deleted_slug}' deleted successfully",
        "branch_slug": deleted_slug,
            }



class DeleteBranchResponse(BaseModel):
    message: str
    branch_slug: str

@router.patch(
    "/{firm_slug}/branches/{branch_slug}",
    response_model=UpdateBranchResponse,
    summary="Update a branch by slug",
)
async def update_one_branch(
    firm_slug: str,
    branch_slug: str,
    data: BranchUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_branch(
        firm_slug=firm_slug,
        branch_slug=branch_slug,
        data=data,
        db=db,
        current_user=current_user,
    )


@router.delete(
    "/{firm_slug}/branches/{branch_slug}",
    response_model=DeleteBranchResponse,
    summary="Delete a branch by slug",
)
async def remove_branch(
    firm_slug: str,
    branch_slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await delete_branch(
        firm_slug=firm_slug,
        branch_slug=branch_slug,
        db=db,
        current_user=current_user,
    )













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









