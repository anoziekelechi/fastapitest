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
