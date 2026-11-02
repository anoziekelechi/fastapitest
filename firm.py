"""Admin business logic."""
from sqlalchemy.ext.asyncio import AsyncSession
from api.core.slug import generate_slug
from api.core.validators import normalize_field
from sqlalchemy.orm import selectinload
from sqlalchemy import desc, text
from sqlmodel import select,func,asc
from fastapi import HTTPException, status
from api.users.schemas import ReadUser, AllUsers
from api.models.users import User, Group
from api.admin.schemas import (
    GroupCreate,
    GroupRead,
    GroupListResponse,
    GroupUpdate,
    UserGroupAssign,
    UserAction,
)

# HELPER

async def get_user_by_email(
    db: AsyncSession,
    email: str,
) -> User | None:
    """Fetch user by email."""
    normalized_email = email.strip().lower()
    result = await db.execute(
        select(User).where(
            User.email == normalized_email
            )
        )
    return result.scalars().first()




async def get_group_by_slug(
    db:AsyncSession,
    slug:str,
)-> Group:
    
    normalized_slug = slug.strip().lower()
    result = await db.execute(
        select(Group).where(Group.slug== normalized_slug)
    )
    group=result.scalars().first()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"group {slug} not found"
        )
    return group


async def get_group_by_name(
    db: AsyncSession,
    name: str,
) -> Group | None:
    
    normalize_name = normalize_field(name)
    result = await db.execute(select(Group).where(Group.name == normalize_name))
    return result.scalars().first()

async def get_group_by_permission(
    db: AsyncSession,
    permission: str,
) -> Group | None:
    
    normalize_permission = normalize_field(permission)
    result = await db.execute(select(Group).where(Group.permission == normalize_permission))
    return result.scalars().first()


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
    existing_name = await get_group_by_name(db, data.name)
    
    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{data.name}' already exists"
        )
    
    # Check permission uniqueness
    
    existing_permission = await get_group_by_permission(db,data.permission)
   
    if existing_permission is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Permission '{data.permission}' already assigned to another group"
        )
    
    group = Group(
        name=data.name,
        permission=data.permission,
        slug=generate_slug(data.name)
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    
    return {"message": f"Group '{group.name}' created successfully",
            "group":GroupRead.model_validate(group)
            }



async def read_single_group(
    db: AsyncSession,
    slug:str,
    current_user: ReadUser, 
) -> GroupRead:
    
    
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action not allowed"
        )
    
    group = await get_group_by_slug(db, slug) 
    return GroupRead.model_validate(group)



async def list_groups(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> GroupListResponse:
    """
    Return total count + paginated list of groups.
    """
    total = (
        await db.execute(
            select(func.count()).select_from(Group)
        )
    ).scalar_one()

    result = await db.execute(
        select(Group)
        .order_by(asc(Group.name))  # type: ignore[arg-type]
        .offset(skip)
        .limit(limit)
    )
    groups = result.scalars().all()

    return GroupListResponse(
        total=total,
        groups=[GroupRead.model_validate(g) for g in groups],
    )






async def delete_group(
    slug: str, 
    db: AsyncSession,
) -> dict:
    """
    Delete a permission group.
    
    Raises:
        400: Group has assigned users
    """
    group = await get_group_by_slug(db, slug)
   
    
    # Prevent deletion if users are assigned
    result=await db.execute(
        select(User.id)
        .where(User.group_id == group.id)
        .limit(1)
    )
    has_users=result.scalar_one_or_none() is not None
    if has_users:
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
  
    user = await get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found"
        )
    
    group = await get_group_by_name(db, data.group_name)
    
    if group is None:
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
    user = await get_user_by_email(db,data.email)
    if user is None:
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
    user = await get_user_by_email(db, data.email)
    if user is None:
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
    user = await get_user_by_email (db, data.email)
   
    if user is None:
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




# =============================================================================
# UPDATE GROUP
# =============================================================================


async def update_group(
    slug: str,
    data: GroupUpdate,
    db: AsyncSession,
) -> GroupRead:
    """
    Update a permission group.

    PATCH-style partial update.

    Only supplied fields are updated.

    If the name changes:
        - Name uniqueness is checked.
        - A new slug is generated.

    Raises:
        404: Group not found.
        400: No changes supplied.
        409: Name, slug, or permission already exists.
    """

    # -------------------------------------------------------------------------
    # Get group
    # -------------------------------------------------------------------------

    group = await get_group_by_slug(
        db,
        slug,
    )

    updated_fields: list[str] = []

    # -------------------------------------------------------------------------
    # Update name
    # -------------------------------------------------------------------------

    if data.name is not None and data.name != group.name:

        existing_name = await get_group_by_name(
            db,
            data.name,
        )

        if (
            existing_name is not None
            and existing_name.id != group.id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group name '{data.name}' already exists",
            )

        # Generate new slug from new name
        new_slug = generate_slug(data.name)

        # Check slug uniqueness
        if new_slug != group.slug:

            result = await db.execute(
                select(Group).where(
                    Group.slug == new_slug,
                    Group.id != group.id,
                )
            )

            existing_slug = result.scalars().first()

            if existing_slug is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Group slug '{new_slug}' already exists"
                    ),
                )

        group.name = data.name
        group.slug = new_slug

        updated_fields.extend(
            ["name", "slug"]
        )

    # -------------------------------------------------------------------------
    # Update permission
    # -------------------------------------------------------------------------

    if (
        data.permission is not None
        and data.permission != group.permission
    ):

        existing_permission = await get_group_by_permission(
            db,
            data.permission,
        )

        if (
            existing_permission is not None
            and existing_permission.id != group.id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Permission '{data.permission}' is already "
                    f"assigned to group '{existing_permission.name}'"
                ),
            )

        group.permission = data.permission
        updated_fields.append("permission")

    # -------------------------------------------------------------------------
    # Reject empty/no-change payload
    # -------------------------------------------------------------------------

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected – all supplied values are "
                "identical to the current values"
            ),
        )

    # -------------------------------------------------------------------------
    # Save
    # -------------------------------------------------------------------------

    await db.commit()
    await db.refresh(group)

    return GroupRead.model_validate(group)


#SCHEMAS



"""Admin schemas."""
import re
from datetime import datetime
from api.core.validators import normalize_field

from pydantic import BaseModel, EmailStr, ConfigDict, field_validator




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
    
    
class GroupListResponse(BaseModel):
    total: int
    groups: list[GroupRead]


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
    #  return normalize_email(v)


class UserAction(BaseModel):
    """Schema for single user actions (disable/enable/remove)."""
    model_config = ConfigDict(extra="forbid")
    
    email: EmailStr
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()
        
    
    
    












