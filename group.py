
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


"""Admin business logic."""

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, asc

from api.admin.schemas import (
    GroupCreate,
    GroupRead,
    GroupListResponse,
    GroupUpdate,
    UserGroupAssign,
    UserAction,
)
from api.core.slug import generate_slug
from api.models.users import User, Group


# =============================================================================
# HELPERS
# =============================================================================


async def get_user_by_email(
    db: AsyncSession,
    email: str,
) -> User | None:
    """
    Fetch a user by email.

    Email is normalized to lowercase before lookup.
    This matches the application's email normalization strategy.
    """

    normalized_email = email.strip().lower()

    result = await db.execute(
        select(User).where(
            User.email == normalized_email
        )
    )

    return result.scalars().first()


async def get_group_by_slug(
    db: AsyncSession,
    slug: str,
) -> Group:
    """
    Fetch a group by slug.

    The incoming slug is normalized by stripping whitespace
    and converting it to lowercase.

    Raises:
        HTTPException: 404 if group is not found.
    """

    normalized_slug = slug.strip().lower()

    result = await db.execute(
        select(Group).where(
            Group.slug == normalized_slug
        )
    )

    group = result.scalars().first()

    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{slug}' not found",
        )

    return group


async def get_group_by_name(
    db: AsyncSession,
    name: str,
) -> Group | None:
    """
    Fetch a group by name, case-insensitively.
    """

    normalized_name = name.strip().lower()

    result = await db.execute(
        select(Group).where(
            func.lower(Group.name) == normalized_name
        )
    )

    return result.scalars().first()


async def get_group_by_permission(
    db: AsyncSession,
    permission: str,
) -> Group | None:
    """
    Fetch a group by permission.
    """

    normalized_permission = permission.strip()

    result = await db.execute(
        select(Group).where(
            Group.permission == normalized_permission
        )
    )

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

    Uniqueness is checked for:
        - name
        - slug
        - permission
    """

    # -------------------------------------------------------------------------
    # Check group name uniqueness
    # -------------------------------------------------------------------------

    existing_name = await get_group_by_name(
        db,
        data.name,
    )

    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{data.name}' already exists",
        )

    # -------------------------------------------------------------------------
    # Generate slug
    # -------------------------------------------------------------------------

    group_slug = generate_slug(data.name)

    # -------------------------------------------------------------------------
    # Check slug uniqueness
    # -------------------------------------------------------------------------

    result = await db.execute(
        select(Group).where(
            Group.slug == group_slug
        )
    )

    existing_slug = result.scalars().first()

    if existing_slug is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group slug '{group_slug}' already exists",
        )

    # -------------------------------------------------------------------------
    # Check permission uniqueness
    # -------------------------------------------------------------------------

    existing_permission = await get_group_by_permission(
        db,
        data.permission,
    )

    if existing_permission is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Permission '{data.permission}' is already "
                f"assigned to group '{existing_permission.name}'"
            ),
        )

    # -------------------------------------------------------------------------
    # Create group
    # -------------------------------------------------------------------------

    group = Group(
        name=data.name,
        permission=data.permission,
        slug=group_slug,
    )

    db.add(group)

    await db.commit()
    await db.refresh(group)

    # -------------------------------------------------------------------------
    # Response
    # -------------------------------------------------------------------------

    return {
        "message": f"Group '{group.name}' created successfully",
        "group": GroupRead.model_validate(group),
    }


# =============================================================================
# READ SINGLE GROUP
# =============================================================================


async def read_single_group(
    db: AsyncSession,
    slug: str,
    current_user: ReadUser,
) -> GroupRead:
    """
    Get a single group by slug.

    Admin only.
    """

    # -------------------------------------------------------------------------
    # Admin check
    # -------------------------------------------------------------------------

    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action not allowed",
        )

    # -------------------------------------------------------------------------
    # Get group
    # -------------------------------------------------------------------------

    group = await get_group_by_slug(
        db,
        slug,
    )

    return GroupRead.model_validate(group)


# =============================================================================
# READ ALL GROUPS
# =============================================================================


async def list_groups(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> GroupListResponse:
    """
    Return total count + paginated list of groups.
    """

    # -------------------------------------------------------------------------
    # Total count
    # -------------------------------------------------------------------------

    total = (
        await db.execute(
            select(func.count()).select_from(Group)
        )
    ).scalar_one()

    # -------------------------------------------------------------------------
    # Paginated groups
    # -------------------------------------------------------------------------

    result = await db.execute(
        select(Group)
        .order_by(asc(Group.name))
        .offset(skip)
        .limit(limit)
    )

    groups = result.scalars().all()

    # -------------------------------------------------------------------------
    # Response
    # -------------------------------------------------------------------------

    return GroupListResponse(
        total=total,
        groups=[
            GroupRead.model_validate(group)
            for group in groups
        ],
    )


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


# =============================================================================
# DELETE GROUP
# =============================================================================


async def delete_group(
    slug: str,
    db: AsyncSession,
) -> dict:
    """
    Delete a permission group.

    A group cannot be deleted while users are assigned to it.

    Raises:
        404: Group not found.
        400: Group has assigned users.
    """

    # -------------------------------------------------------------------------
    # Get group
    # -------------------------------------------------------------------------

    group = await get_group_by_slug(
        db,
        slug,
    )

    # -------------------------------------------------------------------------
    # Check whether users are assigned
    #
    # We only need to know whether at least one user exists.
    # There is no reason to load all users.
    # -------------------------------------------------------------------------

    result = await db.execute(
        select(User.id)
        .where(User.group_id == group.id)
        .limit(1)
    )

    has_users = result.scalar_one_or_none() is not None

    if has_users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot delete group '{group.name}' because "
                "it has assigned users. Remove all users first."
            ),
        )

    # -------------------------------------------------------------------------
    # Delete group
    # -------------------------------------------------------------------------

    group_name = group.name

    await db.delete(group)
    await db.commit()

    # -------------------------------------------------------------------------
    # Response
    # -------------------------------------------------------------------------

    return {
        "message": f"Group '{group_name}' deleted successfully"
    }


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
        404: User or group not found.
        400: User is already in the requested group.
    """

    # -------------------------------------------------------------------------
    # Get user
    # -------------------------------------------------------------------------

    user = await get_user_by_email(
        db,
        data.email,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found",
        )

    # -------------------------------------------------------------------------
    # Get group
    # -------------------------------------------------------------------------

    group = await get_group_by_name(
        db,
        data.group_name,
    )

    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{data.group_name}' not found",
        )

    # -------------------------------------------------------------------------
    # Check whether user is already in this group
    # -------------------------------------------------------------------------

    if user.group_id == group.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"User '{data.email}' is already in "
                f"group '{group.name}'"
            ),
        )

    # -------------------------------------------------------------------------
    # Assign group
    # -------------------------------------------------------------------------

    user.group_id = group.id

    await db.commit()
    await db.refresh(user)

    # -------------------------------------------------------------------------
    # Response
    # -------------------------------------------------------------------------

    return {
        "message": (
            f"User '{data.email}' assigned to "
            f"group '{group.name}'"
        )
    }


# =============================================================================
# REMOVE USER FROM GROUP
# =============================================================================


async def remove_user_from_group(
    data: UserAction,
    db: AsyncSession,
) -> dict:
    """
    Remove a user from their current group.

    Raises:
        404: User not found.
        400: User is not in any group.
    """

    # -------------------------------------------------------------------------
    # Get user
    # -------------------------------------------------------------------------

    user = await get_user_by_email(
        db,
        data.email,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found",
        )

    # -------------------------------------------------------------------------
    # Check whether user belongs to a group
    # -------------------------------------------------------------------------

    if user.group_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is not in any group",
        )

    # -------------------------------------------------------------------------
    # Get current group
    # -------------------------------------------------------------------------

    group = await db.get(
        Group,
        user.group_id,
    )

    group_name = (
        group.name
        if group is not None
        else "Unknown"
    )

    # -------------------------------------------------------------------------
    # Remove group
    # -------------------------------------------------------------------------

    user.group_id = None

    await db.commit()
    await db.refresh(user)

    # -------------------------------------------------------------------------
    # Response
    # -------------------------------------------------------------------------

    return {
        "message": (
            f"User '{data.email}' removed from "
            f"group '{group_name}'"
        )
    }


# =============================================================================
# DISABLE USER
# =============================================================================


async def disable_user(
    data: UserAction,
    db: AsyncSession,
) -> dict:
    """
    Disable a user account.

    Raises:
        404: User not found.
        400: User is already disabled.
    """

    # -------------------------------------------------------------------------
    # Get user
    # -------------------------------------------------------------------------

    user = await get_user_by_email(
        db,
        data.email,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found",
        )

    # -------------------------------------------------------------------------
    # Check current status
    # -------------------------------------------------------------------------

    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is already disabled",
        )

    # -------------------------------------------------------------------------
    # Disable
    # -------------------------------------------------------------------------

    user.disabled = True

    await db.commit()
    await db.refresh(user)

    return {
        "message": f"User '{data.email}' has been disabled"
    }


# =============================================================================
# ENABLE USER
# =============================================================================


async def enable_user(
    data: UserAction,
    db: AsyncSession,
) -> dict:
    """
    Re-enable a disabled user account.

    Raises:
        404: User not found.
        400: User is already active.
    """

    # -------------------------------------------------------------------------
    # Get user
    # -------------------------------------------------------------------------

    user = await get_user_by_email(
        db,
        data.email,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found",
        )

    # -------------------------------------------------------------------------
    # Check current status
    # -------------------------------------------------------------------------

    if not user.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is already active",
        )

    # -------------------------------------------------------------------------
    # Enable
    # -------------------------------------------------------------------------

    user.disabled = False

    await db.commit()
    await db.refresh(user)

    return {
        "message": f"User '{data.email}' has been re-activated"
    }
