# api/payments/schemas.py
"""Payment method schemas."""
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
import re


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


class PaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    country_id: int
    name: str
    slug: str | None = None
    created_at: datetime
    updated_at: datetime


class CountryPaymentMethodsRead(BaseModel):
    """Payment methods grouped by country."""
    country_id: int
    country_name: str
    payment_methods: list[PaymentMethodRead]


class AllPaymentMethodsRead(BaseModel):
    """All payment methods grouped by country."""
    total_countries: int
    data: list[CountryPaymentMethodsRead]




# api/payments/logics.py
"""Payment methods business logic."""
import logging
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from sqlmodel import select

from api.home.models import Country
from api.payments.models import PaymentMethods
from api.payments.schemas import (
    PaymentMethodCreate,
    PaymentMethodUpdate,
    PaymentMethodRead,
    AllPaymentMethodsRead,
    CountryPaymentMethodsRead,
)
from api.users.schemas import ReadUser
from api.core.permissions import Permissions
from api.users.logics import has_permission
from api.core.slug import generate_slug

logger = logging.getLogger(__name__)


async def get_payment_by_slug(
    db: AsyncSession,
    slug: str,
) -> PaymentMethods | None:
    """Fetch payment method by slug."""
    method_id = parse_slug(slug)
    if method_id is not None:
        method = await db.get(PaymentMethods, method_id)
        if method and method.slug == slug:
            return method
    result = await db.execute(
        select(PaymentMethods).where(PaymentMethods.slug == slug)
    )
    return result.scalars().first()


async def create_payment_method(
    data: PaymentMethodCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create payment method for a country.
    Admin or MANAGE_PAYMENTS permission required.
    Country-scoped.
    """
    await has_permission(
        user=current_user,
        required_perm=Permissions.MANAGE_PAYMENTS,
        target_country_id=data.country_id,
    )

    # Validate country exists
    country = await db.get(Country, data.country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {data.country_id} not found"
        )

    # Check uniqueness per country (name + country_id)
    existing = (
        await db.execute(
            select(PaymentMethods).where(
                func.lower(PaymentMethods.name) == data.name.lower(),
                PaymentMethods.country_id == data.country_id,
            )
        )
    ).scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment method '{data.name}' already exists "
                   f"in {country.name}"
        )

    method = PaymentMethods(
        country_id=data.country_id,
        name=data.name,
        slug=generate_slug(data.name),
    )

    try:
        db.add(method)
        await db.commit()
        await db.refresh(method)
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to create payment method: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method"
        )

    logger.info(
        f"Payment method '{method.name}' (slug={method.slug}) "
        f"created in {country.name} by id={current_user.id}"
    )

    return {
        "message": f"Payment method '{method.name}' created "
                   f"successfully in {country.name}",
        "payment_method": PaymentMethodRead.model_validate(method),
    }


async def read_all_payment_methods(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> AllPaymentMethodsRead:
    """
    Return all payment methods grouped by country.
    
    Response:
        Nigeria
            Cash
            Paystack
            Opay
        Liberia
            Momo Liberia
            Orange Money
            Cash
    """
    # Get all countries that have payment methods
    result = await db.execute(
        select(Country)
        .join(PaymentMethods, PaymentMethods.country_id == Country.id)
        .distinct()
        .order_by(Country.name)
        .offset(skip)
        .limit(limit)
    )
    countries = result.scalars().all()

    data = []
    for country in countries:
        # Get payment methods for this country
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

    return AllPaymentMethodsRead(
        total_countries=len(data),
        data=data,
    )


async def read_payment_methods_by_country(
    country_slug: str,
    db: AsyncSession,
) -> CountryPaymentMethodsRead:
    """
    Return all payment methods for a specific country.
    
    Usage: GET /payment-methods/country/1-nigeria
    
    Returns:
        Nigeria
            Cash
            Paystack
            Opay
    """
    from api.home.logics import get_country_by_slug
    country = await get_country_by_slug(db, country_slug)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country '{country_slug}' not found"
        )

    result = await db.execute(
        select(PaymentMethods)
        .where(PaymentMethods.country_id == country.id)
        .order_by(PaymentMethods.name)
    )
    methods = result.scalars().all()

    return CountryPaymentMethodsRead(
        country_id=country.id,
        country_name=country.name,
        payment_methods=[
            PaymentMethodRead.model_validate(m) for m in methods
        ],
    )


async def read_single_payment_method(
    slug: str,
    db: AsyncSession,
) -> PaymentMethodRead:
    """Get single payment method by slug."""
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found"
        )
    return PaymentMethodRead.model_validate(method)


async def update_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> PaymentMethodRead:
    """Update payment method by slug. Country-scoped."""
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found"
        )

    # ✅ Country-scoped permission check
    await has_permission(
        user=current_user,
        required_perm=Permissions.MANAGE_PAYMENTS,
        target_country_id=method.country_id,
    )

    if data.name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided"
        )

    if data.name.lower() == method.name.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New name is the same as current"
        )

    # Check uniqueness in same country
    existing = (
        await db.execute(
            select(PaymentMethods).where(
                func.lower(PaymentMethods.name) == data.name.lower(),
                PaymentMethods.country_id == method.country_id,
                PaymentMethods.id != method.id,
            )
        )
    ).scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment method '{data.name}' already exists in this country"
        )

    method.name = data.name
    method.slug = generate_slug(data.name) 

    try:
        db.add(method)
        await db.commit()
        await db.refresh(method)
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to update payment method: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update payment method"
        )

    logger.info(
        f"Payment method updated: '{old_slug}' → '{method.slug}' "
        f"by id={current_user.id}"
    )

    return PaymentMethodRead.model_validate(method)


async def delete_payment_method(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Delete payment method by slug. Admin only."""
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found"
        )

    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete payment methods"
        )

    method_name = method.name

    try:
        await db.delete(method)
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to delete payment method: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete payment method"
        )

    logger.info(f"Payment method '{method_name}' deleted by id={current_user.id}")





    class Group(BaseModel,table=True):# type: ignore
    __tablename__ = "groups" # type: ignore
    slug: str | None = Field(
        default= None,
        sa_column=Column(String(100),nullable=True, unique=True, index=True)  
    )
    name: str = Field(
        sa_column=Column(String(30),unique=True,nullable=False,index=True)
        )
    permission: str = Field(
        sa_column=Column(String(30),unique=True,nullable=False,index=True)
    )
    
    # one group many users one to many relationship
    users: List["User"] = Relationship(back_populates="group", sa_relationship_kwargs={"passive_deletes":True})






    
async def get_current_user(
    request: Request,
    db: AsyncSession,
) -> ReadUser:
    """
    Get current authenticated user from cookie.
    
    Loads permission ONCE here so all downstream
    has_permission() calls are pure in-memory - no extra DB queries.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )
        

    user = await get_user_by_id(db, payload.sub)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Please contact admin.",
            headers={"X-Error-Code":"account_suspended"},
        )

    if not user.verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not verified. Please verify your email.",
            headers={"X-Error-Code":"account_unverified"},
        )

    # ✅ Load permission ONCE - only 1 extra query for non-admins with a group
    # Admins skip this entirely (no query needed)
    permission: str | None = None
    if not user.is_admin and user.group_id is not None:
        from api.users.models import Group
        group = await db.get(Group, user.group_id)
        if group:
            permission = group.permission

    # ✅ Build ReadUser and inject permission
    read_user = ReadUser.model_validate(user)
    read_user.permission = permission
    return read_user






async def has_permission(
    user: ReadUser,
    required_perm: str,
    target_country_id: int | None = None,
) -> None:
    """
    Pure in-memory permission check.
    No DB queries - permission already loaded in get_current_user.
    
    Hierarchy:
        1. Admin → all permissions ✅
        2. User with matching permission → allowed ✅
        3. Everyone else → 403 ❌
    
    Args:
        user: ReadUser with .permission already set
        required_perm: Required permission string
    
    Raises:
        HTTPException: 403 if permission denied
    """
    # Admins bypass all permission checks
    if user.is_admin:
        return

    # No permission
    if user.permission is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access deny,you dont have permission",
            headers={"X-Error-Code":"no_permission"},
        )
    # wrong permission
    if user.permission != required_perm:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied - requires '{required_perm}' permission",
            headers={"X-Error-Code":"wrong_permission"},
        )
    # Country scope check
    if target_country_id is not None:
        if user.country_id is None:
            raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied no country with such permission",
            headers={"X-Error-Code":"no_country_scope"},
        )
        if user.country_id != target_country_id:
            raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You dont have permission on this country resource,",
            headers={"X-Error-Code":"wrong_country_scope"},
            )
            
            
            
 
    



    # can we rewrite this update with this pattern

     # ------------------------------------------------------------------
    # Reject empty update
    # ------------------------------------------------------------------
    if all(
        value is None
        for value in (
            data.name,
            data.currency_code,
            data.whatsapp,
            data.email_support,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    updated_fields: list[str] = []


    return {"message": f"Payment method '{method_name}' deleted successfully"}
