from pydantic import BaseModel, EmailStr, ConfigDict
from datetime import datetime


class OfficeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    country: str | None = None          # country name, e.g. "Nigeria"
    address: str | None = None
    whatsapp: int | None = None
    phone_number: str | None = None
    email: EmailStr | None = None
    created_at: datetime
    updated_at: datetime


class OfficeListResponse(BaseModel):
    total: int
    country: str | None = None          # which country these offices belong to
    offices: list[OfficeRead]


# Use this in products and offices:
# When you add Nigeria/Ghana domains later, 
#only extend DOMAIN_MAP — no need to change the function
country_name = get_country_name_from_host(request.headers.get("host"))


# api/core/country.py

DEFAULT_COUNTRY_NAME = "Liberia"

DOMAIN_MAP = {
    # Current production + local dev
    "myshop.com": "Liberia",
    "www.myshop.com": "Liberia",
    "localhost": "Liberia",
    "127.0.0.1": "Liberia",
    # Later (uncomment / add when you buy domains)
    # "myshop.ng": "Nigeria",
    # "www.myshop.ng": "Nigeria",
    # "myshop.lr": "Liberia",
    # "www.myshop.lr": "Liberia",
}


def get_country_name_from_host(host: str | None) -> str:
    """Resolve country from request Host header."""
    if not host:
        return DEFAULT_COUNTRY_NAME
    hostname = host.split(":")[0].lower()
    return DOMAIN_MAP.get(hostname, DEFAULT_COUNTRY_NAME)


from fastapi import Request, HTTPException, status
from sqlmodel import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.country import get_country_name_from_host
from api.models.home import Country, Offices  # adjust paths
from api.schemas import OfficeRead, OfficeListResponse  # adjust paths


async def get_all_offices(
    db: AsyncSession,
    request: Request,
    skip: int = 0,
    limit: int = 100,
) -> OfficeListResponse:
    """
    Return offices for the country resolved from the request host.
    Country name match is case-insensitive (Liberia / LIBERIA / liberia).
    """
    host = request.headers.get("host")
    country_name = get_country_name_from_host(host)

    # Case-insensitive match against DB
    result = await db.execute(
        select(Country).where(Country.name.ilike(country_name))
    )
    country = result.scalar_one_or_none()

    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country '{country_name}' not found",
        )

    total = (
        await db.execute(
            select(func.count())
            .select_from(Offices)
            .where(Offices.country_id == country.id)
        )
    ).scalar_one()

    result = await db.execute(
        select(Offices)
        .options(selectinload(Offices.country))  # type: ignore[arg-type]
        .where(Offices.country_id == country.id)
        .order_by(Offices.id)
        .offset(skip)
        .limit(limit)
    )
    offices = result.scalars().all()

    return OfficeListResponse(
        total=total,
        country=country.name,
        offices=[
            OfficeRead(
                id=office.id,  # type: ignore[arg-type]
                country=office.country.name if office.country else None,
                address=office.address,
                whatsapp=office.whatsapp,
                phone_number=office.phone_number,
                email=office.email,
                created_at=office.created_at,
                updated_at=office.updated_at,
            )
            for office in offices
        ],
    )

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_session
from api.offices.logics import get_all_offices  # adjust path
from api.schemas import OfficeListResponse  # adjust path


router = APIRouter(prefix="/offices", tags=["Offices"])


@router.get(
    "/",
    response_model=OfficeListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get offices for current country (from domain)",
)
async def list_offices(
    request: Request,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_session),
) -> OfficeListResponse:
    return await get_all_offices(
        db=db,
        request=request,
        skip=skip,
        limit=limit,
    )


#claude 

# api/home/schemas.py

class OfficeCreate(BaseModel):
    """Schema for creating an office."""
    model_config = ConfigDict(extra="forbid")

    country_id: int = Field(..., gt=0)
    address: str | None = Field(default=None, max_length=500)
    whatsapp: int | None = Field(default=None, gt=0)
    phone_number: str | None = Field(
        default=None,
        min_length=7,
        max_length=20,
    )
    email: EmailStr | None = None

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = re.sub(r"[\s\-()]", "", v)
        if not re.fullmatch(r"\+?[0-9]{7,15}", cleaned):
            raise ValueError(
                "Phone number must be 7-15 digits. "
                "Example: '+2348012345678'"
            )
        return v


class OfficeUpdate(BaseModel):
    """Schema for updating an office - all fields optional."""
    model_config = ConfigDict(extra="forbid")

    address: str | None = None
    whatsapp: int | None = Field(default=None, gt=0)
    phone_number: str | None = None
    email: EmailStr | None = None

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = re.sub(r"[\s\-()]", "", v)
        if not re.fullmatch(r"\+?[0-9]{7,15}", cleaned):
            raise ValueError("Invalid phone number format")
        return v


class OfficeRead(BaseModel):
    """Office response schema."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    country_id: int
    country_name: str | None = None     # ← resolved from relationship
    address: str | None = None
    whatsapp: int | None = None
    phone_number: str | None = None
    email: str | None = None
    created_at: datetime
    updated_at: datetime


class OfficeListRead(BaseModel):
    """Paginated office list response."""
    total: int
    offices: list[OfficeRead]




# api/home/logics.py

from api.core.permissions import Permissions
from api.users.logics import has_permission


# =============================================================================
# OFFICE HELPERS
# =============================================================================

async def get_office_by_id(
    db: AsyncSession,
    office_id: int,
) -> Offices | None:
    """Fetch office by primary key."""
    return await db.get(Offices, office_id)


def _office_to_read(office: Offices) -> OfficeRead:
    """Convert ORM office to OfficeRead schema."""
    return OfficeRead(
        id=office.id,
        country_id=office.country_id,
        country_name=office.country.name if office.country else None,
        address=office.address,
        whatsapp=office.whatsapp,
        phone_number=office.phone_number,
        email=office.email,
        created_at=office.created_at,
        updated_at=office.updated_at,
    )


# =============================================================================
# OFFICE CRUD
# =============================================================================

async def create_office(
    data: OfficeCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a new office.

    Permission:
        Admin → any country
        MANAGE_COUNTRIES → only their assigned country

    Flow:
        1. Permission check (country-scoped for non-admins)
        2. Validate country exists
        3. Check email uniqueness
        4. Create office
    """
    # ✅ Country-scoped permission check
    await has_permission(
        user=current_user,
        required_perm=Permissions.MANAGE_COUNTRIES,
        target_country_id=data.country_id,  # ← scoped to target country
    )

    # Validate country exists
    country = await db.get(Country, data.country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {data.country_id} not found"
        )

    # Check email uniqueness
    if data.email:
        existing_email = (
            await db.execute(
                select(Offices).where(Offices.email == data.email)
            )
        ).scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{data.email}' already used by another office"
            )

    office = Offices(
        country_id=data.country_id,
        address=data.address,
        whatsapp=data.whatsapp,
        phone_number=data.phone_number,
        email=data.email,
    )
    db.add(office)
    await db.commit()
    await db.refresh(office)

    logger.info(
        f"Office created in country_id={data.country_id} "
        f"by {'admin' if current_user.is_admin else 'manager'} "
        f"id={current_user.id}"
    )

    return {
        "message": "Office created successfully",
        "office": _office_to_read(office),
    }


async def read_all_offices(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    country_id: int | None = None,
) -> OfficeListRead:
    """
    List all offices with optional country filter.
    Public - no authentication required.
    """
    query = select(Offices)
    count_query = select(func.count()).select_from(Offices)

    # Filter by country if provided
    if country_id is not None:
        query = query.where(Offices.country_id == country_id)
        count_query = count_query.where(Offices.country_id == country_id)

    total: int = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        query
        .order_by(asc(text("created_at")))
        .offset(skip)
        .limit(limit)
    )
    offices = result.scalars().all()

    return OfficeListRead(
        total=total,
        offices=[_office_to_read(o) for o in offices],
    )


async def read_single_office(
    office_id: int,
    db: AsyncSession,
) -> OfficeRead:
    """
    Get a single office by ID.
    Public - no authentication required.
    """
    office = await get_office_by_id(db, office_id)
    if not office:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Office with ID {office_id} not found"
        )
    return _office_to_read(office)


async def update_office(
    office_id: int,
    data: OfficeUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> OfficeRead:
    """
    Update an office.

    Permission:
        Admin → any office
        MANAGE_COUNTRIES → only offices in their country
    """
    office = await get_office_by_id(db, office_id)
    if not office:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Office with ID {office_id} not found"
        )

    # ✅ Scoped to office's country
    await has_permission(
        user=current_user,
        required_perm=Permissions.MANAGE_COUNTRIES,
        target_country_id=office.country_id,
    )

    if all(v is None for v in [
        data.address, data.whatsapp,
        data.phone_number, data.email
    ]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided"
        )

    updated_fields = []

    if data.address is not None:
        office.address = data.address
        updated_fields.append("address")

    if data.whatsapp is not None:
        office.whatsapp = data.whatsapp
        updated_fields.append("whatsapp")

    if data.phone_number is not None:
        office.phone_number = data.phone_number
        updated_fields.append("phone_number")

    if data.email is not None:
        # Check email uniqueness (exclude current office)
        existing = (
            await db.execute(
                select(Offices)
                .where(Offices.email == data.email)
                .where(Offices.id != office_id)
            )
        ).scalars().first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{data.email}' already used by another office"
            )
        office.email = data.email
        updated_fields.append("email")

    db.add(office)
    await db.commit()
    await db.refresh(office)

    logger.info(
        f"Office {office_id} updated by id={current_user.id}. "
        f"Fields: {', '.join(updated_fields)}"
    )

    return _office_to_read(office)


async def delete_office(
    office_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete an office.

    Permission:
        Admin → any office
        MANAGE_COUNTRIES → only offices in their country
    """
    office = await get_office_by_id(db, office_id)
    if not office:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Office with ID {office_id} not found"
        )

    # ✅ Scoped to office's country
    await has_permission(
        user=current_user,
        required_perm=Permissions.MANAGE_COUNTRIES,
        target_country_id=office.country_id,
    )

    await db.delete(office)
    await db.commit()

    logger.info(
        f"Office {office_id} deleted by id={current_user.id}"
    )

    return {"message": f"Office {office_id} deleted successfully"}




# api/home/routes.py

office_router = APIRouter(prefix="/offices", tags=["Offices"])


# =============================================================================
# PUBLIC ROUTES
# =============================================================================

@office_router.get(
    "",
    response_model=OfficeListRead,
    status_code=status.HTTP_200_OK,
    summary="List all offices",
)
async def list_offices(
    db: DBDep,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    country_id: int | None = Query(
        default=None,
        description="Filter by country ID"
    ),
) -> OfficeListRead:
    """List offices. Optionally filter by country."""
    return await read_all_offices(
        db=db,
        skip=skip,
        limit=limit,
        country_id=country_id,
    )


@office_router.get(
    "/{office_id}",
    response_model=OfficeRead,
    status_code=status.HTTP_200_OK,
    summary="Get single office",
)
async def get_office(
    office_id: int,
    db: DBDep,
) -> OfficeRead:
    """Get a single office by ID."""
    return await read_single_office(office_id=office_id, db=db)


# =============================================================================
# PROTECTED ROUTES
# =============================================================================

@office_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create office (admin or manager)",
)
async def create_office_route(
    data: OfficeCreate,
    db: DBDep,
    current_user: ReadUser = Depends(get_authenticated_user),
) -> dict:
    """
    Create office. Country-scoped:
    - Admin: any country
    - Manager (MANAGE_COUNTRIES): own country only
    """
    return await create_office(
        data=data,
        db=db,
        current_user=current_user,
    )


@office_router.put(
    "/{office_id}",
    response_model=OfficeRead,
    status_code=status.HTTP_200_OK,
    summary="Update office (admin or manager)",
    dependencies=[Depends(require_csrf)],
)
async def update_office_route(
    office_id: int,
    data: OfficeUpdate,
    db: DBDep,
    current_user: ReadUser = Depends(get_authenticated_user),
) -> OfficeRead:
    """
    Update office. Country-scoped:
    - Admin: any office
    - Manager: only offices in their country
    """
    return await update_office(
        office_id=office_id,
        data=data,
        db=db,
        current_user=current_user,
    )


@office_router.delete(
    "/{office_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete office (admin or manager)",
    dependencies=[Depends(require_csrf)],
)
async def delete_office_route(
    office_id: int,
    db: DBDep,
    current_user: ReadUser = Depends(get_authenticated_user),
) -> dict:
    """
    Delete office. Country-scoped:
    - Admin: any office
    - Manager: only offices in their country
    """
    return await delete_office(
        office_id=office_id,
        db=db,
        current_user=current_user,
  )



