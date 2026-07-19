#Read all country without search 

# api/main.py
from api.home.routes import router as country_router

app.include_router(country_router)

class CountryListRead(BaseModel):
    """Paginated country list response."""
    total: int
    countries: list[CountryRead]

async def read_all_countries(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> CountryListRead:
    """
    List all countries with pagination.

    Public - no authentication required.

    Args:
        db: Database session
        skip: Records to skip (pagination offset)
        limit: Maximum records to return

    Returns:
        CountryListRead: Total count + paginated list
    """
    # Get total count
    total: int = (
        await db.execute(select(func.count(Country.id)))
    ).scalar() or 0
    
    # Get paginated results
    result = await db.execute(
        select(Country)
        .order_by(Country.name)
        .offset(skip)
        .limit(limit)
    )
    countries = result.scalars().all()
    
    return CountryListRead(
        total=total,
        countries=[CountryRead.model_validate(c) for c in countries],
    )


@router.get(
    "",
    response_model=CountryListRead,
    status_code=status.HTTP_200_OK,
    summary="List all countries",
)
async def list_countries(
    db: DBDep,
    skip: int = Query(default=0, ge=0, description="Records to skip"),
    limit: int = Query(default=100, ge=1, le=500, description="Max records"),
) -> CountryListRead:
    """
    List all countries with pagination.
    Public endpoint - no authentication required.
    """
    return await read_all_countries(
        db=db,
        skip=skip,
        limit=limit,
    )


# home 
"""Country schemas."""
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# SHARED VALIDATOR
# =============================================================================

def validate_country_name(value: str) -> str:
    """
    Validate and normalize country name.
    
    Rules:
        - Cannot be empty
        - Letters and spaces only (no numbers or special characters)
        - Multiple consecutive spaces collapsed to single space
        - Leading/trailing spaces stripped
        - Each word capitalized (Title Case)
    
    Valid:
        "liberia"           → "Liberia"
        "south africa"      → "South Africa"
        "South   Africa"    → "South Africa"
        "guinea  bissau"    → "Guinea Bissau"
        "  Nigeria  "       → "Nigeria"
    
    Invalid:
        "South123"          → Error (contains number)
        "South@Africa"      → Error (special character)
        ""                  → Error (empty)
    """
    if not value or not value.strip():
        raise ValueError("Country name cannot be empty")
    
    # Strip leading/trailing whitespace
    stripped = value.strip()
    
    # Collapse multiple spaces to single space
    cleaned = re.sub(r" +", " ", stripped)
    
    # Letters and spaces only
    if not re.fullmatch(r"[A-Za-z]+( [A-Za-z]+)*", cleaned):
        raise ValueError(
            "Country name must contain only letters and spaces. "
            "No numbers or special characters allowed. "
            "Example: 'South Africa'"
        )
    
    # Title case: "south africa" → "South Africa"
    return cleaned.title()


def validate_currency_code(value: str) -> str:
    """
    Validate ISO 4217 currency code.
    
    Rules:
        - Exactly 3 letters
        - Letters only
        - Converted to uppercase
    
    Valid:   "lrd" → "LRD", "USD" → "USD", "gbp" → "GBP"
    Invalid: "US"  → Error, "US1" → Error, "USDD" → Error
    """
    if not value or not value.strip():
        raise ValueError("Currency code cannot be empty")
    
    cleaned = value.strip().upper()
    
    if not re.fullmatch(r"[A-Z]{3}", cleaned):
        raise ValueError(
            "Currency code must be exactly 3 letters (ISO 4217 format). "
            "Examples: 'LRD', 'USD', 'GBP', 'NGN'"
        )
    
    return cleaned


# =============================================================================
# COUNTRY SCHEMAS
# =============================================================================

class CountryCreate(BaseModel):
    """Schema for creating a country."""
    model_config = ConfigDict(extra="forbid")
    
    name: str
    currency_code: str
    whatsapp: int | None = Field(default=None, gt=0)
    
    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_country_name(v)
    
    @field_validator("currency_code", mode="before")
    @classmethod
    def validate_code(cls, v: str) -> str:
        return validate_currency_code(v)


class CountryUpdate(BaseModel):
    """Schema for updating a country (all fields optional)."""
    model_config = ConfigDict(extra="forbid")
    
    name: str | None = None
    currency_code: str | None = None
    whatsapp: int | None = Field(default=None, gt=0)
    
    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_country_name(v)
    
    @field_validator("currency_code", mode="before")
    @classmethod
    def validate_code(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_currency_code(v)


class CountryRead(BaseModel):
    """Country response schema."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    name: str
    currency_code: str
    whatsapp: int | None = None
    created_at: datetime
    updated_at: datetime


class CountryListRead(BaseModel):
    """Paginated country list response."""
    total: int
    countries: list[CountryRead]



#logics
"""Country business logic."""
import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func

from api.home.models import Country
from api.home.schemas import (
    CountryCreate,
    CountryUpdate,
    CountryRead,
    CountryListRead,
)
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================

async def get_country_by_id(
    db: AsyncSession,
    country_id: int,
) -> Country | None:
    """Fetch country by primary key."""
    return await db.get(Country, country_id)


async def get_country_by_name(
    db: AsyncSession,
    name: str,
) -> Country | None:
    """Fetch country by name (case-insensitive)."""
    result = await db.execute(
        select(Country).where(
            func.lower(Country.name) == name.lower()
        )
    )
    return result.scalars().first()


# =============================================================================
# ADMIN ACTIONS (Create, Update, Delete)
# =============================================================================

async def create_country(
    data: CountryCreate,
    db: AsyncSession,
    current_user: ReadUser,         # ✅ Admin check done in route via require_admin
) -> dict:
    """
    Create a new country.

    Admin only.

    Flow:
        1. Check name uniqueness (case-insensitive)
        2. Check currency code uniqueness
        3. Create country record

    Args:
        data: Validated country data
        db: Database session
        current_user: Authenticated admin user

    Returns:
        dict: Success message

    Raises:
        HTTPException: 409 if name or currency code already exists
    """
    # Check name uniqueness
    existing_name = await get_country_by_name(db, data.name)
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Country '{data.name}' already exists"
        )
    
    # Check currency code uniqueness
    existing_code = (
        await db.execute(
            select(Country).where(
                func.upper(Country.currency_code) == data.currency_code.upper()
            )
        )
    ).scalars().first()
    if existing_code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Currency code '{data.currency_code}' is already "
                   f"assigned to '{existing_code.name}'"
        )
    
    country = Country(
        name=data.name,
        currency_code=data.currency_code,
        whatsapp=data.whatsapp,
    )
    db.add(country)
    await db.commit()
    await db.refresh(country)
    
    logger.info(
        f"Country '{country.name}' created by admin {current_user.id}"
    )
    
    return {
        "message": f"Country '{country.name}' created successfully",
        "country": CountryRead.model_validate(country),
    }


async def update_country(
    country_id: int,
    data: CountryUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> CountryRead:
    """
    Update an existing country.

    Admin only.

    Flow:
        1. Fetch country (404 if not found)
        2. Validate at least one field provided
        3. Check name uniqueness if name is being changed
        4. Check currency uniqueness if currency is being changed
        5. Apply updates (only provided fields)

    Args:
        country_id: ID of country to update
        data: Partial update data
        db: Database session
        current_user: Authenticated admin user

    Returns:
        CountryRead: Updated country

    Raises:
        HTTPException: 404 if not found
        HTTPException: 400 if no fields provided
        HTTPException: 409 if name or currency already taken
    """
    country = await get_country_by_id(db, country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {country_id} not found"
        )
    
    # At least one field must be provided
    if data.name is None and data.currency_code is None and data.whatsapp is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update"
        )
    
    updated_fields = []
    
    # Update name if provided and different
    if data.name is not None:
        if data.name == country.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New country name is the same as current name"
            )
        # Check uniqueness
        existing = await get_country_by_name(db, data.name)
        if existing and existing.id != country_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists"
            )
        country.name = data.name
        updated_fields.append("name")
    
    # Update currency code if provided and different
    if data.currency_code is not None:
        if data.currency_code == country.currency_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New currency code is the same as current code"
            )
        # Check uniqueness
        existing_code = (
            await db.execute(
                select(Country).where(
                    func.upper(Country.currency_code) == data.currency_code.upper()
                )
            )
        ).scalars().first()
        if existing_code and existing_code.id != country_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Currency code '{data.currency_code}' is already "
                       f"assigned to '{existing_code.name}'"
            )
        country.currency_code = data.currency_code
        updated_fields.append("currency_code")
    
    # Update whatsapp if provided
    if data.whatsapp is not None:
        country.whatsapp = data.whatsapp
        updated_fields.append("whatsapp")
    
    db.add(country)
    await db.commit()
    await db.refresh(country)
    
    logger.info(
        f"Country '{country.name}' updated by admin {current_user.id}. "
        f"Fields: {', '.join(updated_fields)}"
    )
    
    return CountryRead.model_validate(country)


async def delete_country(
    country_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a country.

    Admin only.

    Flow:
        1. Fetch country (404 if not found)
        2. Check no users are assigned to this country
        3. Check no offices are assigned to this country
        4. Delete

    Args:
        country_id: ID of country to delete
        db: Database session
        current_user: Authenticated admin user

    Returns:
        dict: Success message

    Raises:
        HTTPException: 404 if not found
        HTTPException: 400 if country has assigned users or offices
    """
    country = await get_country_by_id(db, country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {country_id} not found"
        )
    
    # Prevent deletion if users are assigned
    if country.users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete '{country.name}' - it has "
                   f"{len(country.users)} assigned user(s). "
                   f"Reassign or remove users first."
        )
    
    # Prevent deletion if offices are assigned
    if country.offices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete '{country.name}' - it has "
                   f"{len(country.offices)} assigned office(s). "
                   f"Remove offices first."
        )
    
    country_name = country.name
    await db.delete(country)
    await db.commit()
    
    logger.info(
        f"Country '{country_name}' (id={country_id}) "
        f"deleted by admin {current_user.id}"
    )
    
    return {"message": f"Country '{country_name}' deleted successfully"}


# =============================================================================
# PUBLIC ACTIONS (Read)
# =============================================================================

async def read_all_countries(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    search: str | None = None,
) -> CountryListRead:
    """
    List all countries with optional search and pagination.

    Public - no authentication required.

    Args:
        db: Database session
        skip: Records to skip (pagination offset)
        limit: Maximum records to return
        search: Optional search term (partial country name match)

    Returns:
        CountryListRead: Total count + paginated list
    """
    # Base query
    query = select(Country).order_by(Country.name)
    count_query = select(func.count(Country.id))
    
    # Apply search filter if provided
    if search:
        search_term = f"%{search.strip()}%"
        query = query.where(Country.name.ilike(search_term))
        count_query = count_query.where(Country.name.ilike(search_term))
    
    # Get total count
    total: int = (await db.execute(count_query)).scalar() or 0
    
    # Get paginated results
    result = await db.execute(query.offset(skip).limit(limit))
    countries = result.scalars().all()
    
    return CountryListRead(
        total=total,
        countries=[CountryRead.model_validate(c) for c in countries],
    )


async def read_single_country(
    country_id: int,
    db: AsyncSession,
) -> CountryRead:
    """
    Get a single country by ID.

    Public - no authentication required.

    Args:
        country_id: Country ID to fetch
        db: Database session

    Returns:
        CountryRead: Country data

    Raises:
        HTTPException: 404 if not found
    """
    country = await get_country_by_id(db, country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {country_id} not found"
        )
    
    return CountryRead.model_validate(country)


#routes

"""Country routes."""
import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import DBDep
from api.core.redis import RedisDep
from api.users.logics import get_current_user, require_admin
from api.users.schemas import ReadUser
from api.home.schemas import (
    CountryCreate,
    CountryUpdate,
    CountryRead,
    CountryListRead,
)
from api.home.logics import (
    create_country,
    update_country,
    delete_country,
    read_all_countries,
    read_single_country,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/countries", tags=["Countries"])


# =============================================================================
# DEPENDENCY
# =============================================================================

async def get_db_session(db: DBDep) -> AsyncSession:
    return db


# Admin-only dependency (reuse from admin module)
AdminUser = Depends(require_admin())


# =============================================================================
# PUBLIC ROUTES (No auth required)
# =============================================================================

@router.get(
    "",
    response_model=CountryListRead,
    status_code=status.HTTP_200_OK,
    summary="List all countries",
)
async def list_countries(
    db: DBDep,
    skip: int = Query(default=0, ge=0, description="Records to skip"),
    limit: int = Query(default=100, ge=1, le=500, description="Max records to return"),
    search: str | None = Query(
        default=None,
        description="Search by country name (partial match)"
    ),
) -> CountryListRead:
    """
    List all countries with pagination and optional search.

    Public endpoint - no authentication required.

    Examples:
        GET /countries                          → all countries
        GET /countries?search=south             → countries containing "south"
        GET /countries?skip=0&limit=10          → first 10 countries
        GET /countries?search=africa&limit=5    → search + paginate
    """
    return await read_all_countries(
        db=db,
        skip=skip,
        limit=limit,
        search=search,
    )


@router.get(
    "/{country_id}",
    response_model=CountryRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single country",
)
async def get_country(
    country_id: int,
    db: DBDep,
) -> CountryRead:
    """
    Get a single country by ID.

    Public endpoint - no authentication required.
    """
    return await read_single_country(
        country_id=country_id,
        db=db,
    )


# =============================================================================
# ADMIN ROUTES (Auth + Admin role required)
# =============================================================================

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a country (admin only)",
)
async def create_country_route(
    data: CountryCreate,
    db: DBDep,
    current_user: ReadUser = AdminUser,             # ✅ Admin only
) -> dict:
    """
    Create a new country.

    Admin only. Country name is normalized to Title Case.
    Multiple spaces are collapsed. Numbers and special chars rejected.

    Examples:
        "liberia"         → "Liberia"
        "south africa"    → "South Africa"
        "guinea  bissau"  → "Guinea Bissau"
    """
    return await create_country(
        data=data,
        db=db,
        current_user=current_user,
    )


@router.put(
    "/{country_id}",
    response_model=CountryRead,
    status_code=status.HTTP_200_OK,
    summary="Update a country (admin only)",
)
async def update_country_route(
    country_id: int,
    data: CountryUpdate,
    db: DBDep,
    current_user: ReadUser = AdminUser,             # ✅ Admin only
) -> CountryRead:
    """
    Update a country.

    Admin only. All fields are optional - only provided fields are updated.
    """
    return await update_country(
        country_id=country_id,
        data=data,
        db=db,
        current_user=current_user,
    )


@router.delete(
    "/{country_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a country (admin only)",
)
async def delete_country_route(
    country_id: int,
    db: DBDep,
    current_user: ReadUser = AdminUser,             # ✅ Admin only
) -> dict:
    """
    Delete a country.

    Admin only. Blocked if country has assigned users or offices.
    """
    return await delete_country(
        country_id=country_id,
        db=db,
        current_user=current_user,
    )


#admin
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
        
    
    
    


##logics 


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





#routes

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


    
    
