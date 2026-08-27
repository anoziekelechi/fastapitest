
"""Admin business logic."""
from sqlalchemy.ext.asyncio import AsyncSession
from api.core.slug import generate_slug
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
    result = await db.execute(select(User).where(User.email == normalized_email))
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
    
   
    result = await db.execute(select(Group).where(Group.name == name.strip()))
    return result.scalars().first()

async def get_permission_by_name(
    db: AsyncSession,
    permission: str,
) -> Group | None:
   
    result = await db.execute(select(Group).where(Group.permission == permission.strip()))
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
    
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{data.name}' already exists"
        )
    
    # Check permission uniqueness
    existing_perm = await get_permission_by_name (db,data.permission)
   
    if existing_perm:
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
    
    return {"message": f"Group '{group.name}' created successfully"}



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



async def update_group(
    group_id: int, # now must use slug
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



