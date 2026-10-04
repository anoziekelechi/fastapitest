# api/payments/logics.py
"""Payment methods business logic."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

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
# ID NARROWING HELPERS
#
# SQLModel types `.id` as `int | None` even on loaded rows. These
# helpers narrow to `int` for the type checker and raise on the
# (impossible) invariant violation.
# =============================================================================

def country_id_of(country: Country) -> int:
    if country.id is None:
        logger.error("Country row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load country. Please try again.",
        )
    return country.id


def payment_method_id_of(method: PaymentMethods) -> int:
    if method.id is None:
        logger.error("Payment method row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )
    return method.id


# =============================================================================
# SLUG HELPER
# =============================================================================

async def get_payment_by_slug(
    db: AsyncSession,
    slug: str,
) -> PaymentMethods | None:
    """Fetch a payment method by slug (name-derived, no id prefix)."""
    try:
        normalized = slug.strip().lower()
        result = await db.execute(
            select(PaymentMethods).where(
                PaymentMethods.slug == normalized
            )
        )
        return result.scalars().first()
    except Exception:
        logger.exception(
            "Failed to fetch payment method slug=%s", slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )


# =============================================================================
# CREATE — manage_payments permission
# =============================================================================

async def create_payment_method(
    data: PaymentMethodCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Create a payment method for a country. Country-scoped."""
    await has_permission(
        user=current_user,
        required_perm="manage_payments",
        target_country_id=data.country_id,
    )

    # Country must exist
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

    # Uniqueness within country (case-insensitive)
    try:
        existing = (
            await db.execute(
                select(PaymentMethods).where(
                    func.lower(PaymentMethods.name) == data.name.lower(),
                    col(PaymentMethods.country_id) == data.country_id,
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

    # Slug is globally unique — prefix with country slug to avoid
    # collisions across countries (e.g. "Cash" in Nigeria + Liberia).
    country_slug = getattr(country, "slug", None) or str(data.country_id)
    method_slug = generate_slug(f"{country_slug}-{data.name}")

    method = PaymentMethods(
        country_id=data.country_id,
        name=data.name,
        slug=method_slug,
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
# READ — all, grouped by country (manage_payments)
# =============================================================================

async def read_all_payment_methods(
    db: AsyncSession,
    current_user: ReadUser,
    skip: int = 0,
    limit: int = 100,
) -> AllPaymentMethodsRead:
    """Return all payment methods grouped by country."""

    await has_permission(
        user=current_user,
        required_perm="manage_payments",
    )

    try:
        # Countries that have at least one payment method.
        # `col()` on both sides so Pyright accepts the join clause.
        result = await db.execute(
            select(Country)
            .join(
                PaymentMethods,
                col(PaymentMethods.country_id) == col(Country.id),
            )
            .distinct()
            .order_by(col(Country.name))
            .offset(skip)
            .limit(limit)
        )
        countries = result.scalars().all()

        data: list[CountryPaymentMethodsRead] = []
        for country in countries:
            methods_result = await db.execute(
                select(PaymentMethods)
                .where(
                    col(PaymentMethods.country_id) == country_id_of(country)
                )
                .order_by(col(PaymentMethods.name))
            )
            methods = methods_result.scalars().all()

            data.append(
                CountryPaymentMethodsRead(
                    country_id=country_id_of(country),
                    country_name=country.name,
                    payment_methods=[
                        PaymentMethodRead.model_validate(m) for m in methods
                    ],
                )
            )
    except HTTPException:
        raise
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
# READ — per country (PUBLIC — registration uses it)
# =============================================================================

async def read_payment_methods_by_country(
    country_slug: str,
    db: AsyncSession,
) -> CountryPaymentMethodsRead:
    """
    Return payment methods for a specific country by country slug.

    Public — no authentication or permission required.
    """

    country = await get_country_by_slug(db, country_slug)

    try:
        result = await db.execute(
            select(PaymentMethods)
            .where(
                col(PaymentMethods.country_id) == country_id_of(country)
            )
            .order_by(col(PaymentMethods.name))
        )
        methods = result.scalars().all()
    except HTTPException:
        raise
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
        country_id=country_id_of(country),
        country_name=country.name,
        payment_methods=[
            PaymentMethodRead.model_validate(m) for m in methods
        ],
    )


# =============================================================================
# READ — single (manage_payments)
# =============================================================================

async def read_single_payment_method(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> PaymentMethodRead:
    """Get a single payment method by slug. Country-scoped permission."""

    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    await has_permission(
        user=current_user,
        required_perm="manage_payments",
        target_country_id=method.country_id,
    )

    return PaymentMethodRead.model_validate(method)


# =============================================================================
# UPDATE — manage_payments (country-scoped)
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
    Returns { message, payment_method }.
    """

    # 1. Load
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    # 2. Country-scoped permission
    await has_permission(
        user=current_user,
        required_perm="manage_payments",
        target_country_id=method.country_id,
    )

    # 3. Reject empty update payload
    if all(value is None for value in (data.name,)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # 4. Track actual changes
    updated_fields: list[str] = []

    if data.name is not None and data.name != method.name:
        # 4a. Uniqueness within the country
        try:
            existing = (
                await db.execute(
                    select(PaymentMethods).where(
                        func.lower(PaymentMethods.name) == data.name.lower(),
                        col(PaymentMethods.country_id) == method.country_id,
                        col(PaymentMethods.id) != payment_method_id_of(method),
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

    # 5. Reject no-op
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # 6. Persist
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
        payment_method_id_of(method),
        current_user.id,
        ",".join(updated_fields),
    )

    return {
        "message": f"Payment method '{method.name}' updated successfully",
        "payment_method": PaymentMethodRead.model_validate(method),
    }


# =============================================================================
# DELETE — manage_payments (country-scoped)
# =============================================================================

async def delete_payment_method(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a payment method by slug.

    Gated by `manage_payments` permission, scoped to the
    payment method's country.
    """

    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    await has_permission(
        user=current_user,
        required_perm="manage_payments",
        target_country_id=method.country_id,
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

    return {
        "message": f"Payment method '{method_name}' deleted successfully"
    }
