
"""Admin routes - require admin authentication."""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import DBDep
from api.users.routes import require_admin
from api.users.schemas import ReadUser
from api.admin.schemas import (
    GroupCreate,
    GroupRead,
    GroupUpdate,
    UserGroupAssign,
    UserAction,
)
from api.admin.logics import (
    create_group,
    list_groups,
    update_group,
    delete_group,
    assign_user_to_group,
    remove_user_from_group,
    disable_user,
    enable_user,
)


router = APIRouter(prefix="/admin", tags=["Admin"])


# =============================================================================
# DEPENDENCY
# =============================================================================
# Admin-only dependency
AdminUser = Depends(require_admin())


# =============================================================================
# GROUP ROUTES
# =============================================================================

@router.post(
    "/groups",
    status_code=status.HTTP_201_CREATED,
    summary="Create permission group",
)
async def create_group_route(
    data: GroupCreate,
    db: DBDep,
    _: ReadUser = AdminUser,         # ✅ Admin check
) -> dict:
    """Create a new permission group."""
    return await create_group(data=data, db=db)


@router.get(
    "/groups",
    response_model=list[GroupRead],
    status_code=status.HTTP_200_OK,
    summary="List all permission groups",
)
async def list_groups_route(
    db: DBDep,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    _: ReadUser = AdminUser,         # ✅ Admin check
) -> list[GroupRead]:
    """List all groups with pagination."""
    return await list_groups(db=db, skip=skip, limit=limit)


@router.put(
    "/groups/{group_id}",
    response_model=GroupRead,
    status_code=status.HTTP_200_OK,
    summary="Update permission group",
)
async def update_group_route(
    group_id: int,
    data: GroupUpdate,
    db: DBDep,
    _: ReadUser = AdminUser,         # ✅ Admin check
) -> GroupRead:
    """Update group name and/or permission."""
    return await update_group(group_id=group_id, data=data, db=db)


@router.delete(
    "/groups/{group_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete permission group",
)
async def delete_group_route(
    group_id: int,
    db: DBDep,
    _: ReadUser = AdminUser,         # ✅ Admin check
) -> dict:
    """Delete a group (must have no assigned users)."""
    return await delete_group(group_id=group_id, db=db)


# =============================================================================
# USER MANAGEMENT ROUTES
# =============================================================================

@router.post(
    "/users/assign-group",
    status_code=status.HTTP_200_OK,
    summary="Assign user to group",
)
async def assign_user_to_group_route(
    data: UserGroupAssign,
    db: DBDep,
    _: ReadUser = AdminUser,         # ✅ Admin check
) -> dict:
    """Assign a user to a permission group."""
    return await assign_user_to_group(data=data, db=db)


@router.post(
    "/users/remove-group",
    status_code=status.HTTP_200_OK,
    summary="Remove user from group",
)
async def remove_from_group_route(
    data: UserAction,
    db: DBDep,
    _: ReadUser = AdminUser,         # ✅ Admin check
) -> dict:
    """Remove a user from their current group."""
    return await remove_user_from_group(data=data, db=db)


@router.post(
    "/users/disable",
    status_code=status.HTTP_200_OK,
    summary="Disable user account",
)
async def disable_user_route(
    data: UserAction,
    db: DBDep,
    _: ReadUser = AdminUser,         # ✅ Admin check
) -> dict:
    """Disable a user account."""
    return await disable_user(data=data, db=db)


@router.post(
    "/users/enable",
    status_code=status.HTTP_200_OK,
    summary="Enable user account",
)
async def enable_user_route(
    data: UserAction,
    db: DBDep,
    _: ReadUser = AdminUser,         # ✅ Admin check
) -> dict:
    """Re-enable a disabled user account."""
    return await enable_user(data=data, db=db)









File "/app/api/admin/routes.py", line 42, in <module>
backend-1  |     @router.post(
backend-1  |      ^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 1063, in decorator
backend-1  |     self.add_api_route(
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 1002, in add_api_route
backend-1  |     route = route_class(
backend-1  |             ^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 621, in __init__
backend-1  |     self.dependant = get_dependant(
backend-1  |                      ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/dependencies/utils.py", line 298, in get_dependant
backend-1  |     sub_dependant = get_dependant(
backend-1  |                     ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/dependencies/utils.py", line 276, in get_dependant
backend-1  |     param_details = analyze_param(
backend-1  |                     ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/dependencies/utils.py", line 501, in analyze_param
backend-1  |     field = create_model_field(
backend-1  |             ^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/utils.py", line 95, in create_model_field
backend-1  |     raise fastapi.exceptions.FastAPIError(
backend-1  | fastapi.exceptions.FastAPIError: Invalid args for response field! Hint: check that <class 'sqlalchemy.ext.asyncio.session.AsyncSession'> is a valid Pydantic field type. If you are using a return type annotation that is not a valid Pydantic field (e.g. Union[Response, dict, None]) you can disable generating the response model from the type annotation with the path operation decorator parameter response_model=None. Read more: https://fastapi.tiangolo.com/tutorial/response-model/


v View in Docker Desktop   o View Config   w Enable Watch





    
    
