
async def create_group(*, db: AsyncSession, data:GroupCreate) -> dict:
    if (await db.exec(select(Group).where(Group.name == data.name))).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{data.name} Group Alredy exist")
    
    if (await db.exec(select(Group).where(Group.permission == data.permission))).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{data.permission} permission  Alredy assigned")
    
    group_perm = Group(
        name = data.name,
        permission = data.permission
    )
    
    db.add(group_perm)
    await db.commit()
    await db.refresh(group_perm)
    
    return{"message":"Group with Permission added successfully"}
    
     
     
     
async def list_group(*, db:AsyncSession, skip: int=0, limit: int=100) -> List[Group]:  
    result = await db.exec(select(Group).order_by(Group.name).offset(skip).limit(limit))
    return result.scalars().all() 


async def update_group(group_id: int, db: AsyncSession,data:GroupUpdate) -> Group: # dict
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="Group not found")
    
    #manual update 100% safe than automatic
    if data.name is not None and data.name != group.name:
        exist = (await db.exec(select(Group).where(Group.name == data.nane))).first()
        if exist:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exist")
        group.name = data.name
        
        
    if data.permission is not None and data.permission != group.permission:
        exist = (await db.exec(select(Group).where(Group.permission == data.permission))).first()
        if exist:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Permission name already exist")
        group.permission = data.permission
        
    await db.commit()
    await db.refresh(group)
    return group
    #in future return dictionary with csrf token
    
    
async def delete_group(group_id:int, db:AsyncSession):
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="Group not found")
    if group.users:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="Cannot delete group with assigned users")
    await db.delete(group)
    await db.commit()
    return{"message":f"Group '{group.name}' deleted"}

async def assign_to_group(data: UserGroup,db:AsyncSession):
    user = (await db.exec(select(User).where(User.email== user.email))).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail=f"User with email {data.email} not found")
    
    group = (await db.exec(select(Group).where(Group.name == data.group_name))).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail=f"Group with name {data.group_name} not found")
    if user.group_id == group.id:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail=f"User already on this group")
    
    user.group_id = group.id
    await db.commit()
    await db.refresh(user)
    return {"message":f"User with {data.email} assigned to {data.group_name}"}

async def remove_user_from_group(data: UsersAction,db: AsyncSession):
    user = (await db.exec(select(User).where(User.email== data.email))).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail=f"User with email {data.email} not found")
    
    if user.group_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="Use not in any group")

    group_name = await db.get(Group, user.group_id).name
    user.group_id = None
    await db.commit()
    await db.refresh(user)
    return {"msg":f"User {data.email} successfully removed from group {group_name}"}

async def disable_user(db:AsyncSession,data:UsersAction):
    user = (await db.exec(select(User).where(User.email== data.email))).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail=f"User with email {data.email} not found")
    
    if user.disabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail=f"User with email {data.email} already disabled")
    user.disabled = True
    await db.commit()
    await db.refresh(user)
    return {"message":f"user {data.email} has been disabled"}

async def enable_user(db:AsyncSession,data:UsersAction):
    user = (await db.exec(select(User).where(User.email== data.email))).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail=f"User with email {data.email} not found")
    
    if not user.disabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail=f"User with email {data.email} is active")
    user.disabled = False
    await db.commit()
    await db.refresh(user)
    return {"message":f"user {data.email} has been re-activated"}
    
    
    
    ###schemas

FORBIDEN_WORDS = {"admin","root","is_admin"}
def normalize_words(value: str) -> str:
    if not value:
        raise ValueError("Field cannot be empty")
    field = value.strip()
    if not field:
        raise ValueError("Field cannot be empty after strip")
     #Accept only safe characters
    if not re.fullmatch(r'^[a-zA-Z_]+',value):
        raise ValueError("only letters and underscore allowed")
    
    #block forbiden words
    lowerd = value.lower()
    for words in FORBIDEN_WORDS:
        if words in lowerd:
            raise ValueError("Invalid names detected")
        
# convert to snake case
    normalized = re.sub(r"[_\s]+", "_",lowerd).strip("_")
    if not normalized:
        raise ValueError("result not found")
    return normalized
    

class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    permission: str
    
    @field_validator("name","permission",mode="before")
    @classmethod
    def validate_name_permission(cls, value: str) -> str:
        return normalize_words(value)


class GroupRead(BaseModel):  
    id:int
    name: str
    permission: str
    date_added: datetime 
    date_modify:datetime   
        # only needed in read
    class Config:
        from_attributes=True
        
    # manufacturers:List[ManufacturerRead]=[]
    # products:List[ProductRead]=[]
    
class GroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str  | None = None
    permission: str | None = None
    
    @field_validator("name","permission",mode="before")
    @classmethod
    def validate_name_permission(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_words(value)
    
    
class UsersGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email:str
    group_name: str
    
    
    
class UsersAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    
    



    
   ###routes

from src.ecommerce.users.model import Group, User
from src.ecommerce.admin.schemas import GroupCreate,GroupRead,UsersGroup, UsersAction,GroupUpdate
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, APIRouter
from src.ecommerce.admin.logics import create_group,remove_user_from_group,disable_user,enable_user,assign_to_group,list_group, \
    update_group,delete_group
from src.ecommerce.users.logics import require_admin
from src.ecommerce.dependency import DB

router = APIRouter(prefix="/admin",tags=["admin"])


@router.post("/add_group",response_model=GroupRead)
async def create_group_perm(
     db: DB,
    data: GroupCreate,
    user: User = Depends(require_admin()),
    
    ):
    return await create_group(data=data,db=db)

@router.get("/list_groups")
async def all_groups(db:DB,user:User = Depends(require_admin()),):
    return await list_group(db=db)

@router.put("/group/{group_id}")
async def update_groups(db:DB,group_id:int,data:GroupUpdate, user: User = Depends(require_admin())):
    return await update_group(group_id=group_id,db=db,data=data)


@router.delete("/delete_group/{group_id}")
async def delete_groups(group_id: int, db:DB,user: User = Depends(require_admin()),):
    return await delete_group(group_id=group_id, db=db)




@router.post("/add_to_group",response_model=GroupRead)
async def create_group_perm(
    data: UsersGroup,
    db:DB,
    user: User = Depends(require_admin()),
  
    ):
    return await assign_to_group(data=data,db=db)

@router.post("/remove_from_group",response_model=GroupRead)
async def create_group_perm(
    data: UsersAction,
    db:DB,
    user: User = Depends(require_admin()),
   
    ):
    return await remove_user_from_group(data=data,db=db)


@router.post("/diable_user",response_model=GroupRead)
async def create_group_perm(
    data: UsersAction,
    db:DB,
    user: User = Depends(require_admin()),
 
    ):
    return await disable_user(data=data,db=db)


@router.post("/enable_user",response_model=GroupRead)
async def create_group_perm(
    data: UsersAction,
    db:DB,
    user: User = Depends(require_admin()),
  
    ):
    return await enable_user(data=data,db=db)



### updated latest admin routes,logics,schema 

"""Admin schemas."""
import re
from datetime import datetime

from pydantic import BaseModel, EmailStr, ConfigDict, field_validator


# =============================================================================
# VALIDATORS
# =============================================================================

FORBIDDEN_WORDS = {"admin", "root", "is_admin", "superuser"}


def normalize_field(value: str) -> str:
    """
    Normalize group name/permission field.
    
    Rules:
    - Letters and underscores only
    - No forbidden words
    - Converted to snake_case lowercase
    """
    if not value or not value.strip():
        raise ValueError("Field cannot be empty")
    
    stripped = value.strip()
    
    # Only letters, spaces, underscores
    if not re.fullmatch(r"[a-zA-Z_\s]+", stripped):
        raise ValueError("Only letters and underscores allowed")
    
    # Block forbidden words
    lowered = stripped.lower()
    for word in FORBIDDEN_WORDS:
        if word in lowered:
            raise ValueError(f"Invalid name: contains forbidden word")
    
    # Convert to snake_case
    normalized = re.sub(r"[\s_]+", "_", lowered).strip("_")
    
    if not normalized:
        raise ValueError("Field cannot be empty after normalization")
    
    return normalized  # ✅ Fixed: was missing return!


# =============================================================================
# GROUP SCHEMAS
# =============================================================================

class GroupCreate(BaseModel):
    """Schema for creating a permission group."""
    model_config = ConfigDict(extra="forbid")
    
    name: str
    permission: str
    
    @field_validator("name", "permission", mode="before")
    @classmethod
    def validate_fields(cls, v: str) -> str:
        return normalize_field(v)


class GroupRead(BaseModel):
    """Group response schema."""
    model_config = ConfigDict(from_attributes=True)  # ✅ Pydantic v2
    
    id: int
    name: str
    permission: str
    created_at: datetime
    updated_at: datetime


class GroupUpdate(BaseModel):
    """Schema for updating a group (all fields optional)."""
    model_config = ConfigDict(extra="forbid")
    
    name: str | None = None
    permission: str | None = None
    
    @field_validator("name", "permission", mode="before")
    @classmethod
    def validate_fields(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_field(v)


# =============================================================================
# USER ACTION SCHEMAS
# =============================================================================

class UserGroupAssign(BaseModel):
    """Schema for assigning user to group."""
    model_config = ConfigDict(extra="forbid")
    
    email: EmailStr      # ✅ EmailStr for validation
    group_name: str
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()


class UserAction(BaseModel):
    """Schema for single user actions (disable/enable/remove)."""
    model_config = ConfigDict(extra="forbid")
    
    email: EmailStr
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()




"""Admin business logic."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from fastapi import HTTPException, status

from api.users.models import User, Group
from api.admin.schemas import (
    GroupCreate,
    GroupRead,
    GroupUpdate,
    UserGroupAssign,
    UserAction,
)


# =============================================================================
# GROUP MANAGEMENT
# =============================================================================

async def create_group(
    data: GroupCreate,
    db: AsyncSession,
) -> dict:
    """
    Create a new permission group.
    
    Raises:
        409: If group name or permission already exists
    """
    # Check name uniqueness
    existing_name = (
        await db.execute(select(Group).where(Group.name == data.name))
    ).scalars().first()
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{data.name}' already exists"
        )
    
    # Check permission uniqueness
    existing_perm = (
        await db.execute(select(Group).where(Group.permission == data.permission))
    ).scalars().first()
    if existing_perm:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Permission '{data.permission}' already assigned to another group"
        )
    
    group = Group(
        name=data.name,
        permission=data.permission,
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    
    return {"message": f"Group '{group.name}' created successfully"}


async def list_groups(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> list[GroupRead]:
    """
    List all permission groups with pagination.
    
    Args:
        skip: Number of records to skip
        limit: Maximum records to return
    """
    result = await db.execute(
        select(Group)
        .order_by(Group.name)
        .offset(skip)
        .limit(limit)
    )
    groups = result.scalars().all()
    return [GroupRead.model_validate(g) for g in groups]


async def update_group(
    group_id: int,
    data: GroupUpdate,
    db: AsyncSession,
) -> GroupRead:
    """
    Update group name and/or permission.
    
    Only updates provided fields.
    Raises:
        404: Group not found
        409: Name or permission already exists
    """
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )
    
    # Update name if provided and different
    if data.name is not None and data.name != group.name:
        existing = (
            await db.execute(select(Group).where(Group.name == data.name))  # ✅ Fixed: data.nane → data.name
        ).scalars().first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group name '{data.name}' already exists"
            )
        group.name = data.name
    
    # Update permission if provided and different
    if data.permission is not None and data.permission != group.permission:
        existing = (
            await db.execute(
                select(Group).where(Group.permission == data.permission)
            )
        ).scalars().first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Permission '{data.permission}' already assigned"
            )
        group.permission = data.permission
    
    await db.commit()
    await db.refresh(group)
    
    return GroupRead.model_validate(group)


async def delete_group(
    group_id: int,
    db: AsyncSession,
) -> dict:
    """
    Delete a permission group.
    
    Raises:
        404: Group not found
        400: Group has assigned users
    """
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )
    
    # Prevent deletion if users are assigned
    if group.users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete group '{group.name}' - it has assigned users. "
                   f"Remove all users first."
        )
    
    group_name = group.name
    await db.delete(group)
    await db.commit()
    
    return {"message": f"Group '{group_name}' deleted successfully"}


# =============================================================================
# USER MANAGEMENT
# =============================================================================

async def assign_user_to_group(
    data: UserGroupAssign,
    db: AsyncSession,
) -> dict:
    """
    Assign a user to a permission group.
    
    Raises:
        404: User or group not found
        400: User already in this group
    """
    # ✅ Fixed: was using user.email (undefined), now data.email
    user = (
        await db.execute(select(User).where(User.email == data.email))
    ).scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found"
        )
    
    group = (
        await db.execute(select(Group).where(Group.name == data.group_name))
    ).scalars().first()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{data.group_name}' not found"
        )
    
    if user.group_id == group.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is already in group '{data.group_name}'"
        )
    
    user.group_id = group.id
    await db.commit()
    await db.refresh(user)
    
    return {
        "message": f"User '{data.email}' assigned to group '{data.group_name}'"
    }


async def remove_user_from_group(
    data: UserAction,
    db: AsyncSession,
) -> dict:
    """
    Remove a user from their current group.
    
    Raises:
        404: User not found
        400: User not in any group
    """
    user = (
        await db.execute(select(User).where(User.email == data.email))
    ).scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found"
        )
    
    if user.group_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is not in any group"
        )
    
    # ✅ Fixed: get group first, then access .name
    group = await db.get(Group, user.group_id)
    group_name = group.name if group else "Unknown"
    
    user.group_id = None
    await db.commit()
    await db.refresh(user)
    
    return {
        "message": f"User '{data.email}' removed from group '{group_name}'"
    }


async def disable_user(
    data: UserAction,
    db: AsyncSession,
) -> dict:
    """
    Disable a user account.
    
    Raises:
        404: User not found
        400: User already disabled
    """
    user = (
        await db.execute(select(User).where(User.email == data.email))
    ).scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found"
        )
    
    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is already disabled"
        )
    
    user.disabled = True
    await db.commit()
    await db.refresh(user)
    
    return {"message": f"User '{data.email}' has been disabled"}


async def enable_user(
    data: UserAction,
    db: AsyncSession,
) -> dict:
    """
    Re-enable a disabled user account.
    
    Raises:
        404: User not found
        400: User already active
    """
    user = (
        await db.execute(select(User).where(User.email == data.email))
    ).scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found"
        )
    
    if not user.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is already active"
        )
    
    user.disabled = False
    await db.commit()
    await db.refresh(user)
    
    return {"message": f"User '{data.email}' has been re-activated"}





"""Admin routes - require admin authentication."""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_session
from api.users.logics import get_current_user, require_admin
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

async def get_db() -> AsyncSession:
    async for session in get_session():
        yield session


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
    db: AsyncSession = Depends(get_db),
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
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
    _: ReadUser = AdminUser,         # ✅ Admin check
) -> dict:
    """Re-enable a disabled user account."""
    return await enable_user(data=data, db=db)



        
    
    
    
    


    
    
