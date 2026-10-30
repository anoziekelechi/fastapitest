async def read_branches_for_firm(
    firm_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> BranchListRead:
    """
    List all branches for a firm, each with its receipt_count.
    """
    guard_user_state(current_user)

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
            detail="Failed to load branches. Please try again.",
        )

    branch_ids = [branch_id_of(b) for b in branches]
    receipt_counts = await receipt_counts_by_branch(db, branch_ids)

    return to_branch_list(firm, branches, receipt_counts)









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









