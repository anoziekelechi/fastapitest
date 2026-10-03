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






