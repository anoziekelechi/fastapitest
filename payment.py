backend/api/templates/
├── layouts/
│   ├── base_email.html           # table-based, inline styles
│   └── base_pdf.html             # modern CSS, <style> tags OK
├── partials/
│   ├── _header.html              # shared branding
│   └── _footer.html              # shared footer
├── emails/
│   ├── otp.html                  # extends layouts/base_email.html
│   ├── welcome.html
│   └── support_message.html
└── receipts/
    └── invoice.html              # extends layouts/base_pdf.html








    # api/payments/schemas.py

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
import re


# =============================================================================
# REQUEST
# =============================================================================

class PaymentMethodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=2, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Payment method name cannot be empty")
        return re.sub(r"\s+", " ", v.strip()).title()


class PaymentMethodUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.strip():
            raise ValueError("Payment method name cannot be empty")
        return re.sub(r"\s+", " ", v.strip()).title()


# =============================================================================
# READ
# =============================================================================

class PaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    country_id: int
    name: str
    slug: str | None = None
    created_at: datetime
    updated_at: datetime


class CountryPaymentMethodsRead(BaseModel):
    country_id: int
    country_name: str
    payment_methods: list[PaymentMethodRead]


class AllPaymentMethodsRead(BaseModel):
    total_countries: int
    data: list[CountryPaymentMethodsRead]


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreatePaymentMethodResponse(BaseModel):
    message: str
    payment_method: PaymentMethodRead


class UpdatePaymentMethodResponse(BaseModel):
    message: str
    payment_method: PaymentMethodRead


class MessageResponse(BaseModel):
    message: str







# api/payments/logics.py
"""Payment methods business logic."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.core.slug import generate_slug
from api.home.logics import get_country_by_slug
from api.home.models import Country
from api.payments.models import PaymentMethods
from api.payments.schemas import (
    AllPaymentMethodsRead,
    CountryPaymentMethodsRead,
    PaymentMethodCreate,
    PaymentMethodRead,
    PaymentMethodUpdate,
)
from api.users.logics import has_permission
from api.users.schemas import ReadUser

logger = logging.getLogger(__name__)


# =============================================================================
# SLUG HELPER
# =============================================================================

async def get_payment_by_slug(
    db: AsyncSession,
    slug: str,
) -> PaymentMethods | None:
    """
    Fetch a payment method by slug.

    The slug format is `{id}-{slugified-name}` (via generate_slug),
    so the numeric prefix can be used for a fast PK lookup.
    """
    try:
        method_id = parse_slug(slug)
        if method_id is not None:
            method = await db.get(PaymentMethods, method_id)
            if method and method.slug == slug:
                return method

        result = await db.execute(
            select(PaymentMethods).where(PaymentMethods.slug == slug)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch payment method slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )


# =============================================================================
# CREATE
# =============================================================================

async def create_payment_method(
    data: PaymentMethodCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a payment method for a country.
    Admin or `manage_payments` permission required. Country-scoped.
    """

    # ------------------------------------------------------------------
    # Permission — direct string from DB
    # ------------------------------------------------------------------
    await has_permission(
        user=current_user,
        required_perm="manage_payments",
        target_country_id=data.country_id,
    )

    # ------------------------------------------------------------------
    # Country must exist
    # ------------------------------------------------------------------
    try:
        country = await db.get(Country, data.country_id)
    except Exception:
        logger.exception(
            "Failed to load country id=%s", data.country_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load country. Please try again.",
        )

    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {data.country_id} not found",
        )

    # ------------------------------------------------------------------
    # Uniqueness within the country
    # ------------------------------------------------------------------
    try:
        existing = (
            await db.execute(
                select(PaymentMethods).where(
                    func.lower(PaymentMethods.name) == data.name.lower(),
                    PaymentMethods.country_id == data.country_id,
                )
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check existing payment method name=%s country_id=%s",
            data.name,
            data.country_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Payment method '{data.name}' already exists "
                f"in {country.name}"
            ),
        )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    method = PaymentMethods(
        country_id=data.country_id,
        name=data.name,
        slug=generate_slug(data.name),
    )

    try:
        db.add(method)
        await db.commit()
        await db.refresh(method)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create payment method name=%s country_id=%s",
            data.name,
            data.country_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    logger.info(
        "Payment method '%s' (slug=%s) created in %s by user_id=%s",
        method.name,
        method.slug,
        country.name,
        current_user.id,
    )

    return {
        "message": (
            f"Payment method '{method.name}' created "
            f"successfully in {country.name}"
        ),
        "payment_method": PaymentMethodRead.model_validate(method),
    }


# =============================================================================
# READ — all, grouped by country
# =============================================================================

async def read_all_payment_methods(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> AllPaymentMethodsRead:
    """
    Return all payment methods grouped by country.
    """

    try:
        # Countries that have at least one payment method
        result = await db.execute(
            select(Country)
            .join(PaymentMethods, PaymentMethods.country_id == Country.id)
            .distinct()
            .order_by(Country.name)
            .offset(skip)
            .limit(limit)
        )
        countries = result.scalars().all()

        data: list[CountryPaymentMethodsRead] = []
        for country in countries:
            methods_result = await db.execute(
                select(PaymentMethods)
                .where(PaymentMethods.country_id == country.id)
                .order_by(PaymentMethods.name)
            )
            methods = methods_result.scalars().all()

            data.append(
                CountryPaymentMethodsRead(
                    country_id=country.id,
                    country_name=country.name,
                    payment_methods=[
                        PaymentMethodRead.model_validate(m) for m in methods
                    ],
                )
            )
    except Exception:
        logger.exception("Failed to list payment methods")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        )

    return AllPaymentMethodsRead(
        total_countries=len(data),
        data=data,
    )


# =============================================================================
# READ — per country
# =============================================================================

async def read_payment_methods_by_country(
    country_slug: str,
    db: AsyncSession,
) -> CountryPaymentMethodsRead:
    """
    Return payment methods for a specific country by country slug.
    """

    country = await get_country_by_slug(db, country_slug)

    try:
        result = await db.execute(
            select(PaymentMethods)
            .where(PaymentMethods.country_id == country.id)
            .order_by(PaymentMethods.name)
        )
        methods = result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load payment methods for country slug=%s",
            country_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        )

    return CountryPaymentMethodsRead(
        country_id=country.id,
        country_name=country.name,
        payment_methods=[
            PaymentMethodRead.model_validate(m) for m in methods
        ],
    )


# =============================================================================
# READ — single
# =============================================================================

async def read_single_payment_method(
    slug: str,
    db: AsyncSession,
) -> PaymentMethodRead:
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )
    return PaymentMethodRead.model_validate(method)


# =============================================================================
# UPDATE
# =============================================================================

async def update_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update a payment method by slug. Country-scoped.

    Only `name` is updatable.

    Returns:
        { message, payment_method }
    """

    # ------------------------------------------------------------------
    # 1. Load the method
    # ------------------------------------------------------------------
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    # ------------------------------------------------------------------
    # 2. Country-scoped permission
    # ------------------------------------------------------------------
    await has_permission(
        user=current_user,
        required_perm="manage_payments",
        target_country_id=method.country_id,
    )

    # ------------------------------------------------------------------
    # 3. Reject empty update payload
    # ------------------------------------------------------------------
    if all(value is None for value in (data.name,)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ------------------------------------------------------------------
    # 4. Track actual changes
    # ------------------------------------------------------------------
    updated_fields: list[str] = []

    if data.name is not None and data.name != method.name:
        # 4a. Uniqueness within the country
        try:
            existing = (
                await db.execute(
                    select(PaymentMethods).where(
                        func.lower(PaymentMethods.name) == data.name.lower(),
                        PaymentMethods.country_id == method.country_id,
                        PaymentMethods.id != method.id,
                    )
                )
            ).scalars().first()
        except Exception:
            logger.exception(
                "Failed to check duplicate payment method name=%s",
                data.name,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update payment method. Please try again.",
            )

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Payment method '{data.name}' already exists "
                    f"in this country"
                ),
            )

        old_slug = method.slug
        method.name = data.name
        method.slug = generate_slug(data.name)

        updated_fields.append("name")

        logger.info(
            "Payment method name changed: slug '%s' -> '%s'",
            old_slug,
            method.slug,
        )

    # ------------------------------------------------------------------
    # 5. Reject no-op update
    # ------------------------------------------------------------------
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ------------------------------------------------------------------
    # 6. Persist
    # ------------------------------------------------------------------
    try:
        db.add(method)
        await db.commit()
        await db.refresh(method)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to update payment method slug=%s", slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update payment method. Please try again.",
        )

    logger.info(
        "Payment method id=%s updated by user_id=%s (fields=%s)",
        method.id,
        current_user.id,
        ",".join(updated_fields),
    )

    return {
        "message": f"Payment method '{method.name}' updated successfully",
        "payment_method": PaymentMethodRead.model_validate(method),
    }


# =============================================================================
# DELETE
# =============================================================================

async def delete_payment_method(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a payment method by slug. Admin only.
    """

    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete payment methods",
            headers={"X-Error-Code": "not_admin"},
        )

    method_name = method.name

    try:
        await db.delete(method)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to delete payment method slug=%s", slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete payment method. Please try again.",
        )

    logger.info(
        "Payment method '%s' deleted by user_id=%s",
        method_name,
        current_user.id,
    )

    return {"message": f"Payment method '{method_name}' deleted successfully"}





# api/payments/routes.py

from fastapi import APIRouter, status

from api.core.database import DBDep
from api.payments.logics import (
    create_payment_method,
    delete_payment_method,
    read_all_payment_methods,
    read_payment_methods_by_country,
    read_single_payment_method,
    update_payment_method,
)
from api.payments.schemas import (
    AllPaymentMethodsRead,
    CountryPaymentMethodsRead,
    CreatePaymentMethodResponse,
    MessageResponse,
    PaymentMethodCreate,
    PaymentMethodRead,
    PaymentMethodUpdate,
    UpdatePaymentMethodResponse,
)
from api.users.deps import CurrentUser


router = APIRouter(prefix="/payment-methods", tags=["Payment Methods"])


# =============================================================================
# PUBLIC READS
# =============================================================================

@router.get(
    "",
    response_model=AllPaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List all payment methods grouped by country",
)
async def list_all_payment_methods(
    db: DBDep,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_payment_methods(db=db, skip=skip, limit=limit)


@router.get(
    "/country/{country_slug}",
    response_model=CountryPaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List payment methods for a specific country",
)
async def list_payment_methods_by_country(country_slug: str, db: DBDep):
    return await read_payment_methods_by_country(
        country_slug=country_slug, db=db
    )


@router.get(
    "/{slug}",
    response_model=PaymentMethodRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single payment method",
)
async def get_payment_method(slug: str, db: DBDep):
    return await read_single_payment_method(slug=slug, db=db)


# =============================================================================
# AUTHENTICATED MUTATIONS
# =============================================================================

@router.post(
    "",
    response_model=CreatePaymentMethodResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment method (permission: manage_payments)",
)
async def create_method(
    data: PaymentMethodCreate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await create_payment_method(
        data=data, db=db, current_user=current_user
    )


@router.patch(
    "/{slug}",
    response_model=UpdatePaymentMethodResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a payment method (permission: manage_payments)",
)
async def patch_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_payment_method(
        slug=slug, data=data, db=db, current_user=current_user
    )


@router.delete(
    "/{slug}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a payment method (admin only)",
)
async def remove_payment_method(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await delete_payment_method(
        slug=slug, db=db, current_user=current_user
    )






# api/firms/schemas.py
"""Firm schemas - validators imported from validators.py."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.core.validators import validate_international_phone


# =============================================================================
# REQUEST
# =============================================================================

class FirmCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=200)
    registration_number: str | None = Field(default=None, max_length=100)
    address: str = Field(..., min_length=5, max_length=500)
    phone_number: str
    deals_on: str = Field(..., min_length=10, max_length=2000)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Firm name cannot be empty")
        return v.strip()

    @field_validator("address", mode="before")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Address cannot be empty")
        return v.strip()

    @field_validator("deals_on", mode="before")
    @classmethod
    def validate_deals_on(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Deals on cannot be empty")
        return v.strip()

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        result = validate_international_phone(v)
        if result is None:
            raise ValueError("Phone number is required")
        return result

    @field_validator("registration_number", mode="before")
    @classmethod
    def validate_reg_number(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped if stripped else None


class FirmUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    registration_number: str | None = None
    address: str | None = None
    phone_number: str | None = None
    deals_on: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        return v.strip() if v else None

    @field_validator("address", mode="before")
    @classmethod
    def validate_address(cls, v: str | None) -> str | None:
        return v.strip() if v else None

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        return validate_international_phone(v)

    @field_validator("deals_on", mode="before")
    @classmethod
    def validate_deals_on(cls, v: str | None) -> str | None:
        return v.strip() if v else None


# =============================================================================
# READ
# =============================================================================

class FirmRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    slug: str | None = None
    registration_number: str | None = None
    address: str
    phone_number: str
    deals_on: str
    created_at: datetime
    updated_at: datetime


class FirmListRead(BaseModel):
    total: int
    firms: list[FirmRead]


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreateFirmResponse(BaseModel):
    message: str
    firm: FirmRead


class UpdateFirmResponse(BaseModel):
    message: str
    firm: FirmRead


class MessageResponse(BaseModel):
    message: str






# api/firms/logics.py
"""Firm business logic."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.core.slug import generate_slug, parse_slug
from api.firms.models import Firm
from api.firms.schemas import (
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
)
from api.users.schemas import ReadUser

logger = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================

async def get_firm_by_slug(
    db: AsyncSession,
    slug: str,
) -> Firm | None:
    """
    Fetch a firm by slug.

    Slug format is `{id}-{slugified-name}` (via generate_slug), so
    the numeric prefix allows a fast PK lookup before falling back
    to a slug query.
    """
    try:
        firm_id = parse_slug(slug)
        if firm_id is not None:
            firm = await db.get(Firm, firm_id)
            if firm and firm.slug == slug:
                return firm

        result = await db.execute(
            select(Firm).where(Firm.slug == slug)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )


# =============================================================================
# CREATE
# =============================================================================

async def create_firm(
    data: FirmCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a firm profile. A user may own multiple firms.
    """

    user_id = current_user.id

    # ------------------------------------------------------------------
    # Name uniqueness (global)
    # ------------------------------------------------------------------
    try:
        existing_name = (
            await db.execute(
                select(Firm).where(func.lower(Firm.name) == data.name.lower())
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check existing firm name=%s", data.name
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create firm. Please try again.",
        )

    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Firm name '{data.name}' is already taken",
        )

    # ------------------------------------------------------------------
    # Registration number uniqueness
    # ------------------------------------------------------------------
    if data.registration_number:
        try:
            existing_reg = (
                await db.execute(
                    select(Firm).where(
                        Firm.registration_number == data.registration_number
                    )
                )
            ).scalars().first()
        except Exception:
            logger.exception(
                "Failed to check existing registration_number=%s",
                data.registration_number,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create firm. Please try again.",
            )

        if existing_reg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Registration number '{data.registration_number}' "
                    f"is already registered"
                ),
            )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    firm = Firm(
        user_id=user_id,
        name=data.name,
        slug=generate_slug(data.name),
        registration_number=data.registration_number,
        address=data.address,
        phone_number=data.phone_number,
        deals_on=data.deals_on,
    )

    try:
        db.add(firm)
        await db.commit()
        await db.refresh(firm)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create firm for user_id=%s", user_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create firm profile. Please try again.",
        )

    logger.info(
        "Firm '%s' (slug=%s) created by user_id=%s",
        firm.name,
        firm.slug,
        user_id,
    )

    return {
        "message": f"Firm '{firm.name}' created successfully",
        "firm": FirmRead.model_validate(firm),
    }


# =============================================================================
# READ
# =============================================================================

async def read_my_firms(
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmListRead:
    """Get all firms owned by the authenticated user."""
    try:
        result = await db.execute(
            select(Firm)
            .where(Firm.user_id == current_user.id)
            .order_by(Firm.name)
        )
        firms = result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s", current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load your firms. Please try again.",
        )

    return FirmListRead(
        total=len(firms),
        firms=[FirmRead.model_validate(f) for f in firms],
    )


async def read_all_firms(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> FirmListRead:
    """List all firms. Public."""
    try:
        total: int = (
            await db.execute(select(func.count()).select_from(Firm))
        ).scalar() or 0

        result = await db.execute(
            select(Firm).order_by(Firm.name).offset(skip).limit(limit)
        )
        firms = result.scalars().all()
    except Exception:
        logger.exception("Failed to list firms")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firms. Please try again.",
        )

    return FirmListRead(
        total=total,
        firms=[FirmRead.model_validate(f) for f in firms],
    )


async def read_single_firm(
    slug: str,
    db: AsyncSession,
) -> FirmRead:
    """Get a single firm by slug. Public."""
    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )
    return FirmRead.model_validate(firm)


# =============================================================================
# UPDATE
# =============================================================================

async def update_firm(
    slug: str,
    data: FirmUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update a firm. Owner or admin only.

    Returns { message, firm }.
    """

    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    # ------------------------------------------------------------------
    # Ownership check
    # ------------------------------------------------------------------
    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    # ------------------------------------------------------------------
    # Reject empty update
    # ------------------------------------------------------------------
    if all(
        v is None
        for v in (
            data.name,
            data.registration_number,
            data.address,
            data.phone_number,
            data.deals_on,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ------------------------------------------------------------------
    # Track changed fields
    # ------------------------------------------------------------------
    updated_fields: list[str] = []

    # --- name (+ slug) ---
    if data.name is not None and data.name != firm.name:
        try:
            existing = (
                await db.execute(
                    select(Firm).where(
                        func.lower(Firm.name) == data.name.lower(),
                        Firm.id != firm.id,
                    )
                )
            ).scalars().first()
        except Exception:
            logger.exception(
                "Failed to check duplicate firm name=%s", data.name
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update firm. Please try again.",
            )

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Firm name '{data.name}' is already taken",
            )

        old_slug = firm.slug
        firm.name = data.name
        firm.slug = generate_slug(data.name)
        updated_fields.append("name")
        logger.info(
            "Firm slug updated: '%s' -> '%s'", old_slug, firm.slug
        )

    # --- registration_number ---
    if (
        data.registration_number is not None
        and data.registration_number != firm.registration_number
    ):
        try:
            existing_reg = (
                await db.execute(
                    select(Firm).where(
                        Firm.registration_number == data.registration_number,
                        Firm.id != firm.id,
                    )
                )
            ).scalars().first()
        except Exception:
            logger.exception(
                "Failed to check duplicate registration_number=%s",
                data.registration_number,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update firm. Please try again.",
            )

        if existing_reg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Registration number '{data.registration_number}' "
                    f"is already registered"
                ),
            )

        firm.registration_number = data.registration_number
        updated_fields.append("registration_number")

    # --- address ---
    if data.address is not None and data.address != firm.address:
        firm.address = data.address
        updated_fields.append("address")

    # --- phone_number ---
    if (
        data.phone_number is not None
        and data.phone_number != firm.phone_number
    ):
        firm.phone_number = data.phone_number
        updated_fields.append("phone_number")

    # --- deals_on ---
    if data.deals_on is not None and data.deals_on != firm.deals_on:
        firm.deals_on = data.deals_on
        updated_fields.append("deals_on")

    # ------------------------------------------------------------------
    # Reject no-op update
    # ------------------------------------------------------------------
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    try:
        db.add(firm)
        await db.commit()
        await db.refresh(firm)
    except Exception:
        await db.rollback()
        logger.exception("Failed to update firm slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update firm profile. Please try again.",
        )

    logger.info(
        "Firm '%s' updated by user_id=%s (fields=%s)",
        firm.name,
        current_user.id,
        ",".join(updated_fields),
    )

    return {
        "message": f"Firm '{firm.name}' updated successfully",
        "firm": FirmRead.model_validate(firm),
    }


# =============================================================================
# DELETE
# =============================================================================

async def delete_firm(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Delete a firm. Owner or admin only."""

    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    firm_name = firm.name

    try:
        await db.delete(firm)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete firm slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete firm. Please try again.",
        )

    logger.info(
        "Firm '%s' deleted by user_id=%s", firm_name, current_user.id
    )

    return {"message": f"Firm '{firm_name}' deleted successfully"}







# api/firms/routes.py

from fastapi import APIRouter, status

from api.core.database import DBDep
from api.firms.logics import (
    create_firm,
    delete_firm,
    read_all_firms,
    read_my_firms,
    read_single_firm,
    update_firm,
)
from api.firms.schemas import (
    CreateFirmResponse,
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
    MessageResponse,
    UpdateFirmResponse,
)
from api.users.deps import CurrentUser


router = APIRouter(prefix="/firms", tags=["Firms"])


# =============================================================================
# PUBLIC READS
# =============================================================================

@router.get(
    "",
    response_model=FirmListRead,
    status_code=status.HTTP_200_OK,
    summary="List all firms (public)",
)
async def list_firms(
    db: DBDep,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_firms(db=db, skip=skip, limit=limit)


@router.get(
    "/{slug}",
    response_model=FirmRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single firm (public)",
)
async def get_firm(slug: str, db: DBDep):
    return await read_single_firm(slug=slug, db=db)


# =============================================================================
# AUTHENTICATED READS
# =============================================================================

@router.get(
    "/me/mine",
    response_model=FirmListRead,
    status_code=status.HTTP_200_OK,
    summary="List firms owned by the authenticated user",
)
async def list_my_firms(db: DBDep, current_user: CurrentUser):
    return await read_my_firms(db=db, current_user=current_user)


# =============================================================================
# AUTHENTICATED MUTATIONS
# =============================================================================

@router.post(
    "",
    response_model=CreateFirmResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a firm (authenticated)",
)
async def create(
    data: FirmCreate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await create_firm(data=data, db=db, current_user=current_user)


@router.patch(
    "/{slug}",
    response_model=UpdateFirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a firm (owner or admin)",
)
async def patch_firm(
    slug: str,
    data: FirmUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_firm(
        slug=slug, data=data, db=db, current_user=current_user
    )


@router.delete(
    "/{slug}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a firm (owner or admin)",
)
async def remove_firm(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await delete_firm(
        slug=slug, db=db, current_user=current_user
    )
