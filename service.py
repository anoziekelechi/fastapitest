# api/subscriptions/logics.py
"""Subscription business logic."""

import logging
import secrets
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, UploadFile, status
from redis.asyncio import Redis
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from api.core.file_storage import (
    SERVICE_IMAGE,
    SERVICE_IMAGE_MAX_SIZE,
    delete_from_r2,
    get_public_url,
    handle_file_update,
)
from api.core.guards import guard_firm_state, guard_user_state
from api.core.settings import get_settings
from api.core.slug import generate_slug
from api.models.home import PaymentMethods
from api.models.subscription import (
    Subscription,
    SubscriptionPayment,
    SubscriptionPlan,
    SubscriptionService,
    SubscriptionTier,
)
from api.models.users import Firm
from api.payments.gateways import initiate_payment, verify_payment
from api.subscription.schemas import (
    PlanCreate,
    PlanRead,
    PlanUpdate,
    ServiceCreate,
    ServiceRead,
    ServiceUpdate,
    SubscriptionActivate,
    SubscriptionPaymentConfirm,
    SubscriptionPaymentRead,
    SubscriptionPurchase,
    SubscriptionRead,
    SubscriptionStatusRead,
    SubscriptionUpdate,
    TierCreate,
    TierRead,
    TierUpdate,
    TierWithPlansRead,
)
from api.subscriptions.helpers import (
    get_subscription_status,
    utc_now,
)
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)
settings = get_settings()

# How long a pending payment stays valid before it can be replaced
PENDING_PAYMENT_TTL_MINUTES = 15

# How long an idempotency key survives (Redis)
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


# =============================================================================
# COMMON HELPERS
# =============================================================================

def _load_relationship(attr: Any):
    """
    Wrap `selectinload` so Pyright accepts SQLModel relationships.
    Runtime behavior is unaffected.
    """
    return selectinload(attr)


def _database_error(operation: str, exc: SQLAlchemyError) -> HTTPException:
    logger.exception("Database failure during %s", operation, exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="A database error occurred. Please try again.",
    )


def _is_admin(user: ReadUser | None) -> bool:
    return bool(user and getattr(user, "is_admin", False))


def _service_id(service: SubscriptionService) -> int:
    if service.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The service has no database ID.",
        )
    return service.id


def _tier_id(tier: SubscriptionTier) -> int:
    if tier.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The tier has no database ID.",
        )
    return tier.id


def _plan_id(plan: SubscriptionPlan) -> int:
    if plan.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The plan has no database ID.",
        )
    return plan.id


def _payment_id(payment: SubscriptionPayment) -> int:
    if payment.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The payment has no database ID.",
        )
    return payment.id


async def _commit_or_rollback(
    db: AsyncSession,
    *,
    operation: str,
) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("Constraint violation during %s", operation)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The operation conflicts with an existing record.",
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error(operation, exc) from exc


async def _refresh_or_raise(
    db: AsyncSession,
    instance: Any,
    *,
    operation: str,
) -> None:
    try:
        await db.refresh(instance)
    except SQLAlchemyError as exc:
        raise _database_error(operation, exc) from exc


async def _count_subscriptions(
    db: AsyncSession,
    **filters: Any,
) -> int:
    try:
        query = select(func.count()).select_from(Subscription)
        for field_name, value in filters.items():
            query = query.where(getattr(Subscription, field_name) == value)
        result = await db.execute(query)
        return int(result.scalar_one())
    except SQLAlchemyError as exc:
        raise _database_error("counting subscriptions", exc) from exc


# =============================================================================
# RESPONSE BUILDERS
# =============================================================================

def to_plan_read(plan: SubscriptionPlan) -> PlanRead:
    """Build a PlanRead with currency supplied by application settings."""
    result = PlanRead.model_validate(plan)
    return result.model_copy(
        update={"currency": settings.currency_code}
    )


def to_service_read(service: SubscriptionService) -> ServiceRead:
    """Build a ServiceRead with the logo URL resolved from its key."""
    read = ServiceRead.model_validate(service)
    return read.model_copy(
        update={
            "service_logo_url": (
                get_public_url(service.service_logo_key)
                if service.service_logo_key
                else None
            ),
        }
    )


def _compute_subscription_read(sub: Subscription) -> SubscriptionRead:
    remaining, label, can_access = get_subscription_status(
        is_active=sub.is_active,
        is_on_trial=sub.is_on_trial,
        trial_ends_at=sub.trial_ends_at,
        subscription_ends_at=sub.subscription_ends_at,
    )

    return SubscriptionRead.model_validate(sub).model_copy(
        update={
            "days_remaining": remaining,
            "status_label": label,
            "can_access": can_access,
        }
    )


# =============================================================================
# LOOKUP HELPERS
# =============================================================================

async def get_service_by_slug(
    db: AsyncSession,
    slug: str,
) -> SubscriptionService:
    try:
        result = await db.execute(
            select(SubscriptionService).where(
                SubscriptionService.slug == slug
            )
        )
        service = result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error("looking up a service by slug", exc) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )
    return service


async def get_tier_by_id(
    db: AsyncSession,
    tier_id: int,
) -> SubscriptionTier:
    try:
        result = await db.execute(
            select(SubscriptionTier).where(
                SubscriptionTier.id == tier_id
            )
        )
        tier = result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error("looking up a tier", exc) from exc

    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription tier not found.",
        )
    return tier


async def get_plan_by_id(
    db: AsyncSession,
    plan_id: int,
) -> SubscriptionPlan:
    try:
        result = await db.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.id == plan_id
            )
        )
        plan = result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error("looking up a plan", exc) from exc

    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription plan not found.",
        )
    return plan


async def get_subscription_by_firm_service(
    db: AsyncSession,
    firm_id: int,
    service_id: int,
) -> Subscription | None:
    try:
        result = await db.execute(
            select(Subscription).where(
                Subscription.firm_id == firm_id,
                Subscription.service_id == service_id,
            )
        )
        return result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error(
            "looking up a firm's subscription", exc
        ) from exc


async def get_pending_payment(
    db: AsyncSession,
    firm_id: int,
    plan_id: int,
) -> SubscriptionPayment | None:
    """
    Return the pending payment for (firm_id, plan_id), ignoring any
    that have exceeded PENDING_PAYMENT_TTL_MINUTES.

    Stale pendings are marked "expired" here so they stop blocking
    new purchases.
    """
    cutoff = utc_now() - timedelta(minutes=PENDING_PAYMENT_TTL_MINUTES)

    try:
        result = await db.execute(
            select(SubscriptionPayment).where(
                col(SubscriptionPayment.firm_id) == firm_id,
                col(SubscriptionPayment.plan_id) == plan_id,
                col(SubscriptionPayment.status) == "pending",
            )
        )
        payment = result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error("looking up a pending payment", exc) from exc

    if payment is None:
        return None

    if payment.created_at < cutoff:
        # Stale — mark expired so it doesn't block new purchases
        payment.status = "expired"
        db.add(payment)
        try:
            await db.commit()
        except SQLAlchemyError:
            await db.rollback()
            logger.exception(
                "Failed to expire stale pending payment ref=%s",
                payment.payment_reference,
            )
        return None

    return payment


async def _service_has_buyable(
    db: AsyncSession,
    service_id: int,
) -> bool:
    """True if the service has ≥ 1 active tier with ≥ 1 active plan."""
    query = (
        select(SubscriptionTier.id)
        .join(
            SubscriptionPlan,
            col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),
        )
        .where(
            col(SubscriptionTier.service_id) == service_id,
            col(SubscriptionTier.is_active).is_(True),
            col(SubscriptionPlan.is_active).is_(True),
        )
        .limit(1)
    )
    try:
        result = await db.execute(query)
        return result.scalars().first() is not None
    except SQLAlchemyError as exc:
        raise _database_error(
            "checking service availability", exc
        ) from exc


# =============================================================================
# SERVICE — CREATE (admin)
# =============================================================================

async def create_service(
    data: ServiceCreate,
    service_image: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin-only. Enforced by route via `admin: AdminUser`."""

    try:
        result = await db.execute(
            select(SubscriptionService).where(
                col(SubscriptionService.name) == data.name,
            )
        )
        existing = result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error("creating a service", exc) from exc

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Subscription service '{data.name}' already exists.",
        )

    new_image_key: str | None = None
    if service_image is not None:
        new_image_key = await handle_file_update(
            file=service_image,
            current_key=None,
            prefix=SERVICE_IMAGE,
            max_size=SERVICE_IMAGE_MAX_SIZE,
        )

    service = SubscriptionService(
        name=data.name,
        description=data.description,
        service_logo_key=new_image_key,
        trial_days=data.trial_days,
        is_active=True,
        slug=generate_slug(data.name),
    )

    def cleanup_new_image() -> None:
        if new_image_key is None:
            return
        try:
            delete_from_r2(new_image_key)
        except Exception:
            logger.exception(
                "Failed to clean up service image key=%s",
                new_image_key,
            )

    try:
        db.add(service)
        await db.flush()
        await db.refresh(service)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        cleanup_new_image()
        logger.warning(
            "Service creation violated a DB constraint: name=%s",
            data.name,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Subscription service '{data.name}' already exists.",
        )
    except SQLAlchemyError:
        await db.rollback()
        cleanup_new_image()
        logger.exception(
            "Database error creating service name=%s", data.name
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create service. Please try again.",
        )
    except Exception:
        await db.rollback()
        cleanup_new_image()
        logger.exception(
            "Unexpected error creating service name=%s", data.name
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create service. Please try again.",
        )

    logger.info(
        "Service '%s' (slug=%s) created by admin user_id=%s",
        service.name,
        service.slug,
        current_user.id,
    )

    return {
        "message": f"Subscription service '{service.name}' created successfully.",
        "service": to_service_read(service),
    }


# =============================================================================
# SERVICE — READ (list, buyable only for public)
# =============================================================================

async def read_services(
    db: AsyncSession,
    *,
    current_user: ReadUser | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[ServiceRead]:
    """
    List services.

    Public callers see only services that are active AND have ≥ 1
    active tier with ≥ 1 active plan.
    Admins see all services.
    """
    query = (
        select(SubscriptionService)
        .order_by(col(SubscriptionService.name))
        .offset(skip)
        .limit(limit)
    )

    if not _is_admin(current_user):
        buyable_exists = (
            select(SubscriptionTier.id)
            .join(
                SubscriptionPlan,
                col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),
            )
            .where(
                col(SubscriptionTier.service_id)
                == col(SubscriptionService.id),
                col(SubscriptionTier.is_active).is_(True),
                col(SubscriptionPlan.is_active).is_(True),
            )
            .exists()
        )
        query = query.where(
            col(SubscriptionService.is_active).is_(True),
            buyable_exists,
        )

    try:
        result = await db.execute(query)
        services = result.scalars().all()
    except SQLAlchemyError as exc:
        raise _database_error("reading subscription services", exc) from exc

    return [to_service_read(s) for s in services]


# =============================================================================
# SERVICE — READ (detail, buyable only for public)
# =============================================================================

async def read_service_by_slug(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceRead:
    service = await get_service_by_slug(db, slug)

    if not _is_admin(current_user):
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subscription service '{slug}' not found.",
            )
        buyable = await _service_has_buyable(db, _service_id(service))
        if not buyable:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subscription service '{slug}' not found.",
            )

    return to_service_read(service)


# =============================================================================
# SERVICE — READ (with tiers + plans)
# =============================================================================

async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> dict:
    """
    Service + tiers + plans.

    Public: 404 if the service is not buyable.
    Admin: sees everything.
    """
    service = await get_service_by_slug(db, slug)
    is_admin = _is_admin(current_user)

    if not is_admin:
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )
        buyable = await _service_has_buyable(db, _service_id(service))
        if not buyable:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )

    tier_query = (
        select(SubscriptionTier)
        .where(col(SubscriptionTier.service_id) == _service_id(service))
        .options(_load_relationship(SubscriptionTier.plans))
        .order_by(col(SubscriptionTier.name))
    )
    if not is_admin:
        tier_query = tier_query.where(
            col(SubscriptionTier.is_active).is_(True)
        )

    try:
        result = await db.execute(tier_query)
        tiers = result.scalars().all()
    except SQLAlchemyError as exc:
        raise _database_error("reading service tiers", exc) from exc

    tier_reads: list[TierWithPlansRead] = []
    for tier in tiers:
        plans = sorted(tier.plans or [], key=lambda p: p.duration_days)
        visible_plans = [
            p for p in plans if is_admin or p.is_active
        ]
        # Public: skip tiers with zero visible plans
        if not is_admin and not visible_plans:
            continue
        tier_reads.append(
            TierWithPlansRead.model_validate(tier).model_copy(
                update={
                    "plans": [to_plan_read(p) for p in visible_plans],
                }
            )
        )

    return {
        **to_service_read(service).model_dump(),
        "tiers": [t.model_dump() for t in tier_reads],
    }


# =============================================================================
# SERVICE — UPDATE (admin)
# =============================================================================

async def update_service(
    slug: str,
    data: ServiceUpdate,
    db: AsyncSession,
    service_image: UploadFile | None = None,
) -> dict:
    service = await get_service_by_slug(db, slug)

    if all(
        v is None
        for v in (
            data.description,
            data.trial_days,
            service_image,
            data.is_active,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    old_image_key = service.service_logo_key
    new_image_key: str | None = None

    if service_image is not None:
        try:
            new_image_key = await handle_file_update(
                file=service_image,
                current_key=None,
                prefix=SERVICE_IMAGE,
                max_size=SERVICE_IMAGE_MAX_SIZE,
            )
            if not new_image_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the service image.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "Service image upload failed: service_id=%s", service.id
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload service image.",
            ) from exc

    updated_fields: list[str] = []

    if data.description is not None and data.description != service.description:
        service.description = data.description
        updated_fields.append("description")

    if data.trial_days is not None and data.trial_days != service.trial_days:
        service.trial_days = data.trial_days
        updated_fields.append("trial_days")

    if data.is_active is not None and data.is_active != service.is_active:
        service.is_active = data.is_active
        updated_fields.append("is_active")

    if new_image_key is not None and new_image_key != service.service_logo_key:
        service.service_logo_key = new_image_key
        updated_fields.append("service_logo_key")

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    try:
        await db.flush()
    except SQLAlchemyError as exc:
        rollback_ok = False
        try:
            await db.rollback()
            rollback_ok = True
        except Exception:
            logger.exception(
                "Rollback failed after service flush error: service_id=%s",
                service.id,
            )
        if rollback_ok and new_image_key:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up image key=%s", new_image_key
                )
        logger.exception(
            "Flush error updating service id=%s (rollback_ok=%s)",
            service.id,
            rollback_ok,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update service. Please try again.",
        ) from exc
    except Exception as exc:
        rollback_ok = False
        try:
            await db.rollback()
            rollback_ok = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error: service_id=%s",
                service.id,
            )
        if rollback_ok and new_image_key:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up image key=%s", new_image_key
                )
        logger.exception(
            "Unexpected error updating service id=%s", service.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating service.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        # Commit outcome uncertain — do NOT delete the new image
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after commit error: service_id=%s",
                service.id,
            )
        logger.exception(
            "Service commit failed / uncertain: service_id=%s, image=%s",
            service.id,
            new_image_key,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service update could not be confirmed. "
                "Check its state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(service)
    except Exception:
        logger.exception(
            "Failed to refresh service after update: service_id=%s",
            service.id,
        )

    if (
        new_image_key
        and old_image_key
        and new_image_key != old_image_key
    ):
        try:
            delete_from_r2(old_image_key)
        except Exception:
            logger.exception(
                "Old service image not deleted: service_id=%s, key=%s",
                service.id,
                old_image_key,
            )

    logger.info(
        "Service updated: service_id=%s, fields=%s",
        service.id,
        ",".join(updated_fields),
    )

    return {
        "message": "Service updated successfully.",
        "service": to_service_read(service),
    }


# =============================================================================
# SERVICE — DELETE (admin)
# =============================================================================

async def delete_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    service = await get_service_by_slug(db, slug)

    count = await _count_subscriptions(db, service_id=_service_id(service))
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This service cannot be deleted because subscriptions "
                "reference it."
            ),
        )

    name = service.name
    image_key = service.service_logo_key

    try:
        await db.delete(service)
        await _commit_or_rollback(db, operation="deleting a service")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("deleting a service", exc) from exc

    if image_key:
        try:
            delete_from_r2(image_key)
        except Exception:
            logger.exception(
                "Failed to delete service image key=%s", image_key
            )

    logger.info(
        "Service '%s' deleted by user_id=%s", name, current_user.id
    )

    return {"message": f"'{name}' subscription service deleted successfully."}


# =============================================================================
# TIER — CREATE
# =============================================================================

async def create_tier(
    data: TierCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    try:
        service = await db.get(SubscriptionService, data.service_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading a service for tier creation", exc) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    try:
        existing = await db.execute(
            select(SubscriptionTier).where(
                SubscriptionTier.service_id == data.service_id,
                func.lower(SubscriptionTier.name) == data.name.lower(),
            )
        )
        if existing.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A tier with this name already exists for the service.",
            )

        tier = SubscriptionTier(
            service_id=data.service_id,
            name=data.name,
            description=data.description,
            slug=generate_slug(
                f"{service.slug or service.name}-{data.name}"
            ),
            is_active=True,
        )
        db.add(tier)
        await _commit_or_rollback(db, operation="creating a tier")
        await _refresh_or_raise(db, tier, operation="refreshing a tier")

        return {
            "message": "Subscription tier created successfully.",
            "tier": TierRead.model_validate(tier),
        }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("creating a tier", exc) from exc


# =============================================================================
# TIER — READ
# =============================================================================

async def read_tiers_for_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> list[TierRead]:
    """
    List tiers for a service.

    Public: only active tiers that have ≥ 1 active plan.
    Admin: all tiers.
    """
    service = await get_service_by_slug(db, slug)
    is_admin = _is_admin(current_user)

    if not service.is_active and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    query = (
        select(SubscriptionTier)
        .where(col(SubscriptionTier.service_id) == _service_id(service))
        .order_by(col(SubscriptionTier.name))
    )

    if not is_admin:
        has_active_plan = (
            select(SubscriptionPlan.id)
            .where(
                col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),
                col(SubscriptionPlan.is_active).is_(True),
            )
            .exists()
        )
        query = query.where(
            col(SubscriptionTier.is_active).is_(True),
            has_active_plan,
        )

    try:
        result = await db.execute(query)
        return [TierRead.model_validate(t) for t in result.scalars().all()]
    except SQLAlchemyError as exc:
        raise _database_error("reading service tiers", exc) from exc


async def read_tier_by_id(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> TierRead:
    tier = await get_tier_by_id(db, tier_id)
    is_admin = _is_admin(current_user)

    if not is_admin:
        if not tier.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription tier not found.",
            )

        try:
            service = await db.get(SubscriptionService, tier.service_id)
        except SQLAlchemyError as exc:
            raise _database_error("loading a service", exc) from exc

        if service is None or not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription tier not found.",
            )

    return TierRead.model_validate(tier)


# =============================================================================
# TIER — UPDATE
# =============================================================================

async def update_tier(
    tier_id: int,
    data: TierUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    tier = await get_tier_by_id(db, tier_id)
    changes = data.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided.",
        )

    changed = False
    for field_name, value in changes.items():
        if getattr(tier, field_name) != value:
            setattr(tier, field_name, value)
            changed = True

    if not changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made.",
        )

    await _commit_or_rollback(db, operation="updating a tier")
    await _refresh_or_raise(db, tier, operation="refreshing a tier")

    return {
        "message": "Subscription tier updated successfully.",
        "tier": TierRead.model_validate(tier),
    }


# =============================================================================
# TIER — DELETE
# =============================================================================

async def delete_tier(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    tier = await get_tier_by_id(db, tier_id)

    count = await _count_subscriptions(db, tier_id=_tier_id(tier))
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This tier cannot be deleted because subscriptions "
                "reference it."
            ),
        )

    try:
        await db.delete(tier)
        await _commit_or_rollback(db, operation="deleting a tier")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("deleting a tier", exc) from exc

    return {"message": "Subscription tier deleted successfully."}


# =============================================================================
# PLAN — CREATE
# =============================================================================

async def create_plan(
    data: PlanCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    try:
        tier = await db.get(SubscriptionTier, data.tier_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading a tier for plan creation", exc) from exc

    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription tier not found.",
        )

    try:
        existing = await db.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.tier_id == data.tier_id,
                func.lower(SubscriptionPlan.name) == data.name.lower(),
            )
        )
        if existing.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A plan with this name already exists for the tier.",
            )

        plan = SubscriptionPlan(
            tier_id=data.tier_id,
            name=data.name,
            description=data.description,
            duration_days=data.duration_days,
            price=data.price,
            slug=generate_slug(
                f"{tier.slug or tier.name}-{data.name}"
            ),
            is_active=True,
        )
        db.add(plan)
        await _commit_or_rollback(db, operation="creating a plan")
        await _refresh_or_raise(db, plan, operation="refreshing a plan")

        return {
            "message": "Subscription plan created successfully.",
            "plan": to_plan_read(plan),
        }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("creating a plan", exc) from exc


# =============================================================================
# PLAN — READ
# =============================================================================

async def read_plans_for_tier(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> list[PlanRead]:
    tier = await get_tier_by_id(db, tier_id)
    is_admin = _is_admin(current_user)

    if not is_admin:
        if not tier.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription tier not found.",
            )
        try:
            service = await db.get(SubscriptionService, tier.service_id)
        except SQLAlchemyError as exc:
            raise _database_error("loading a service", exc) from exc
        if service is None or not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription tier not found.",
            )

    query = (
        select(SubscriptionPlan)
        .where(col(SubscriptionPlan.tier_id) == _tier_id(tier))
        .order_by(col(SubscriptionPlan.duration_days))
    )
    if not is_admin:
        query = query.where(col(SubscriptionPlan.is_active).is_(True))

    try:
        result = await db.execute(query)
        return [to_plan_read(p) for p in result.scalars().all()]
    except SQLAlchemyError as exc:
        raise _database_error("reading tier plans", exc) from exc


async def read_plan_by_id(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> PlanRead:
    plan = await get_plan_by_id(db, plan_id)
    is_admin = _is_admin(current_user)

    if not is_admin:
        if not plan.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )
        try:
            tier = await db.get(SubscriptionTier, plan.tier_id)
        except SQLAlchemyError as exc:
            raise _database_error("loading a tier", exc) from exc
        if tier is None or not tier.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )
        try:
            service = await db.get(SubscriptionService, tier.service_id)
        except SQLAlchemyError as exc:
            raise _database_error("loading a service", exc) from exc
        if service is None or not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )

    return to_plan_read(plan)


# =============================================================================
# PLAN — UPDATE
# =============================================================================

async def update_plan(
    plan_id: int,
    data: PlanUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    plan = await get_plan_by_id(db, plan_id)
    changes = data.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided.",
        )

    changed = False
    for field_name, value in changes.items():
        if getattr(plan, field_name) != value:
            setattr(plan, field_name, value)
            changed = True

    if not changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made.",
        )

    await _commit_or_rollback(db, operation="updating a plan")
    await _refresh_or_raise(db, plan, operation="refreshing a plan")

    return {
        "message": "Subscription plan updated successfully.",
        "plan": to_plan_read(plan),
    }


# =============================================================================
# PLAN — DELETE
# =============================================================================

async def delete_plan(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    plan = await get_plan_by_id(db, plan_id)

    count = await _count_subscriptions(db, plan_id=_plan_id(plan))
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This plan cannot be deleted because subscriptions "
                "reference it."
            ),
        )

    try:
        await db.delete(plan)
        await _commit_or_rollback(db, operation="deleting a plan")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("deleting a plan", exc) from exc

    return {"message": "Subscription plan deleted successfully."}


# =============================================================================
# ADMIN — ACTIVATE (override, no payment)
# =============================================================================

async def activate_subscription(
    data: SubscriptionActivate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Admin override — grants or extends a subscription without a
    payment record. Enforced by route via `admin: AdminUser`.

    Does NOT set `last_payment_id` (there is no payment involved).
    """
    guard_user_state(current_user)

    try:
        firm = await db.get(Firm, data.firm_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading the firm", exc) from exc

    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm with ID {data.firm_id} not found.",
        )
    guard_firm_state(firm)

    service = await db.get(SubscriptionService, data.service_id)
    if not service or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Service with ID {data.service_id} not found or inactive."
            ),
        )

    tier = await get_tier_by_id(db, data.tier_id)
    if not tier.is_active or tier.service_id != data.service_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Tier '{tier.name}' does not belong to the specified "
                "service or is inactive."
            ),
        )

    plan = await get_plan_by_id(db, data.plan_id)
    if not plan.is_active or plan.tier_id != data.tier_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Plan '{plan.name}' does not belong to the specified "
                "tier or is inactive."
            ),
        )

    now = utc_now()
    ends_at = now + timedelta(days=plan.duration_days)

    sub = await get_subscription_by_firm_service(
        db, data.firm_id, data.service_id
    )

    if sub is None:
        sub = Subscription(
            firm_id=data.firm_id,
            service_id=data.service_id,
            tier_id=data.tier_id,
            plan_id=data.plan_id,
            is_on_trial=False,
            trial_started_at=now,
            trial_ends_at=None,
            is_active=True,
            subscription_started_at=now,
            subscription_ends_at=ends_at,
            auto_renew=False,
        )
        db.add(sub)
    else:
        same_plan = (
            sub.tier_id == data.tier_id
            and sub.plan_id == data.plan_id
            and not sub.is_on_trial
            and sub.is_active
            and sub.subscription_ends_at
            and sub.subscription_ends_at > now
        )
        if same_plan:
            ends_at = sub.subscription_ends_at + timedelta(
                days=plan.duration_days
            )
            sub.subscription_ends_at = ends_at
        else:
            sub.tier_id = data.tier_id
            sub.plan_id = data.plan_id
            sub.is_on_trial = False
            sub.trial_ends_at = None
            sub.is_active = True
            sub.subscription_started_at = now
            sub.subscription_ends_at = ends_at
        db.add(sub)

    await _commit_or_rollback(db, operation="activating a subscription")
    await _refresh_or_raise(db, sub, operation="refreshing a subscription")

    logger.info(
        "Admin activated subscription: firm_id=%s service_id=%s "
        "tier_id=%s plan_id=%s by user_id=%s",
        data.firm_id,
        data.service_id,
        data.tier_id,
        data.plan_id,
        current_user.id,
    )

    return {
        "message": (
            f"Subscription activated for '{service.name}' until "
            f"{ends_at.strftime('%Y-%m-%d')}"
        ),
        "subscription": _compute_subscription_read(sub),
    }


# =============================================================================
# PURCHASE — INITIATE (user-facing, idempotent)
# =============================================================================

async def initiate_subscription_purchase(
    data: SubscriptionPurchase,
    db: AsyncSession,
    redis: Redis,
    current_user: ReadUser,
) -> dict:
    """
    Start a subscription purchase. USER-FACING — no admin required.

    Idempotency:
        - Redis caches (idempotency_key → payment_reference) for 24h.
        - A repeated call with the same key returns the original
          payment info instead of creating a new one.
        - A pending payment for the same (firm, plan) blocks a new
          initiate. Stale pendings (older than PENDING_PAYMENT_TTL)
          are expired automatically and no longer block.

    Creates a SubscriptionPayment(status="pending"), calls the
    gateway, returns the reference.
    """
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 0. Idempotency — same key → return the cached response
    # ------------------------------------------------------------------
    idem_key = (
        f"purchase_idem:{current_user.id}:{data.idempotency_key}"
    )

    try:
        cached_reference = await redis.get(idem_key)
    except Exception:
        logger.exception("Redis read failed for idempotency key")
        cached_reference = None

    if cached_reference:
        if isinstance(cached_reference, bytes):
            cached_reference = cached_reference.decode("utf-8")

        try:
            result = await db.execute(
                select(SubscriptionPayment).where(
                    col(SubscriptionPayment.payment_reference)
                    == cached_reference
                )
            )
            existing_payment = result.scalars().first()
        except SQLAlchemyError as exc:
            raise _database_error("loading a cached payment", exc) from exc

        if existing_payment:
            return {
                "message": "Payment already initiated.",
                "payment_reference": existing_payment.payment_reference,
                "amount": existing_payment.amount,
                "currency": existing_payment.currency,
                "status": existing_payment.status,
                "provider_url": None,
            }

    # ------------------------------------------------------------------
    # 1. Firm — exists, owned, enabled
    # ------------------------------------------------------------------
    try:
        firm = await db.get(Firm, data.firm_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading the firm", exc) from exc

    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm with ID {data.firm_id} not found.",
        )
    if firm.user_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only subscribe for your own firms.",
            headers={"X-Error-Code": "wrong_permission"},
        )
    guard_firm_state(firm)

    # ------------------------------------------------------------------
    # 2. Plan → tier → service chain
    # ------------------------------------------------------------------
    plan = await get_plan_by_id(db, data.plan_id)
    if not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan with ID {data.plan_id} not found or inactive.",
        )

    tier = await get_tier_by_id(db, plan.tier_id)
    if not tier.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tier with ID {plan.tier_id} not found or inactive.",
        )

    try:
        service = await db.get(SubscriptionService, tier.service_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading the service", exc) from exc

    if not service or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Service with ID {tier.service_id} not found or inactive."
            ),
        )

    # ------------------------------------------------------------------
    # 3. Payment method
    # ------------------------------------------------------------------
    try:
        payment_method = await db.get(
            PaymentMethods, data.payment_method_id
        )
    except SQLAlchemyError as exc:
        raise _database_error("loading the payment method", exc) from exc

    if not payment_method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Payment method with ID {data.payment_method_id} "
                "not found."
            ),
        )

    # ------------------------------------------------------------------
    # 4. Existing pending payment (with staleness check)
    # ------------------------------------------------------------------
    existing_pending = await get_pending_payment(
        db, data.firm_id, data.plan_id
    )

    if existing_pending:
        # Cache the existing reference under this key so the client
        # can resume from the same flow.
        try:
            await redis.set(
                idem_key,
                existing_pending.payment_reference,
                ex=IDEMPOTENCY_TTL_SECONDS,
            )
        except Exception:
            logger.exception("Redis write failed for idempotency key")

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A payment for this plan is already pending. "
                "Please complete it or wait for it to expire."
            ),
            headers={"X-Error-Code": "payment_pending"},
        )

    # ------------------------------------------------------------------
    # 5. Create the pending payment
    # ------------------------------------------------------------------
    reference = f"sub_{secrets.token_urlsafe(16)}"

    payment = SubscriptionPayment(
        firm_id=data.firm_id,
        plan_id=data.plan_id,
        payment_method_id=data.payment_method_id,
        amount=plan.price,
        currency=settings.currency_code,
        payment_reference=reference,
        status="pending",
    )

    try:
        db.add(payment)
        await _commit_or_rollback(
            db, operation="creating a subscription payment"
        )
        await _refresh_or_raise(
            db, payment, operation="refreshing a subscription payment"
        )
    except Exception:
        raise

    # ------------------------------------------------------------------
    # 6. Cache the idempotency key
    # ------------------------------------------------------------------
    try:
        await redis.set(idem_key, reference, ex=IDEMPOTENCY_TTL_SECONDS)
    except Exception:
        logger.exception(
            "Redis write failed for idempotency key=%s", idem_key
        )

    # ------------------------------------------------------------------
    # 7. Call the gateway
    # ------------------------------------------------------------------
    provider = payment_method.name.lower().replace(" ", "_")

    try:
        gateway_response = await initiate_payment(
            provider=provider,
            amount=str(plan.price),
            currency=settings.currency_code,
            reference=reference,
        )
    except Exception:
        logger.exception(
            "Gateway initiation failed for payment ref=%s", reference
        )

        payment.status = "failed"
        db.add(payment)
        try:
            await db.commit()
        except Exception:
            await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment gateway is unavailable. Please try again.",
        )

    logger.info(
        "Subscription purchase initiated: firm_id=%s plan_id=%s "
        "ref=%s provider=%s",
        data.firm_id,
        data.plan_id,
        reference,
        provider,
    )

    return {
        "message": "Payment initiated. Complete the payment to activate.",
        "payment_reference": reference,
        "amount": plan.price,
        "currency": settings.currency_code,
        "status": gateway_response.get("status", "pending"),
        "provider_url": gateway_response.get("provider_url"),
    }


# =============================================================================
# PURCHASE — CONFIRM (user-facing, idempotent, row-locked)
# =============================================================================

async def confirm_subscription_payment(
    data: SubscriptionPaymentConfirm,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Confirm a payment and activate the subscription.

    USER-FACING — no admin required.

    Idempotency:
        - Row-level lock (FOR UPDATE) serializes concurrent confirms
          for the same payment reference.
        - `paid` payments return 409 immediately; no double activation.
        - `pending` payments proceed through gateway verification.
        - `failed` / `expired` payments return 400.

    Sets Subscription.last_payment_id to trace the payment.
    """
    # ------------------------------------------------------------------
    # 1. Load payment WITH row lock
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(SubscriptionPayment)
            .where(
                col(SubscriptionPayment.payment_reference)
                == data.payment_reference
            )
            .with_for_update()
        )
        payment = result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error("loading the payment", exc) from exc

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment '{data.payment_reference}' not found.",
        )

    # ------------------------------------------------------------------
    # 2. Firm + ownership
    # ------------------------------------------------------------------
    try:
        firm = await db.get(Firm, payment.firm_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading the firm", exc) from exc

    if not firm:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Firm not found for this payment.",
        )
    if firm.user_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only confirm payments for your own firms.",
            headers={"X-Error-Code": "wrong_permission"},
        )

    guard_user_state(current_user)
    guard_firm_state(firm)

    # ------------------------------------------------------------------
    # 3. Idempotency / state check
    # ------------------------------------------------------------------
    if payment.status == "paid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This payment has already been confirmed.",
        )

    if payment.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"This payment cannot be confirmed "
                f"(status: {payment.status})."
            ),
        )

    # ------------------------------------------------------------------
    # 4. Load payment method + verify with gateway
    # ------------------------------------------------------------------
    try:
        payment_method = await db.get(
            PaymentMethods, payment.payment_method_id
        )
    except SQLAlchemyError as exc:
        raise _database_error("loading the payment method", exc) from exc

    if not payment_method:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment method not found for this payment.",
        )

    provider = payment_method.name.lower().replace(" ", "_")

    try:
        verification = await verify_payment(
            provider=provider,
            reference=payment.payment_reference,
        )
    except Exception:
        logger.exception(
            "Gateway verification failed ref=%s",
            payment.payment_reference,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment gateway is unavailable. Please try again.",
        )

    if verification.get("status") != "paid":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Payment has not been confirmed by the gateway.",
            headers={"X-Error-Code": "payment_pending"},
        )

    # ------------------------------------------------------------------
    # 5. Load plan → tier
    # ------------------------------------------------------------------
    plan = await get_plan_by_id(db, payment.plan_id)
    tier = await get_tier_by_id(db, plan.tier_id)
    service_id = tier.service_id

    # ------------------------------------------------------------------
    # 6. Mark payment paid
    # ------------------------------------------------------------------
    now = utc_now()
    payment.status = "paid"
    payment.paid_at = now
    db.add(payment)

    # ------------------------------------------------------------------
    # 7. Activate / extend subscription (inline)
    # ------------------------------------------------------------------
    sub = await get_subscription_by_firm_service(
        db, payment.firm_id, service_id
    )

    ends_at = now + timedelta(days=plan.duration_days)

    if sub is None:
        sub = Subscription(
            firm_id=payment.firm_id,
            service_id=service_id,
            tier_id=plan.tier_id,
            plan_id=_plan_id(plan),
            last_payment_id=_payment_id(payment),   # traceability
            is_on_trial=False,
            trial_started_at=now,
            trial_ends_at=None,
            is_active=True,
            subscription_started_at=now,
            subscription_ends_at=ends_at,
            auto_renew=False,
        )
        db.add(sub)
    else:
        same_plan = (
            sub.tier_id == plan.tier_id
            and sub.plan_id == _plan_id(plan)
            and not sub.is_on_trial
            and sub.is_active
            and sub.subscription_ends_at
            and sub.subscription_ends_at > now
        )
        if same_plan:
            ends_at = sub.subscription_ends_at + timedelta(
                days=plan.duration_days
            )
            sub.subscription_ends_at = ends_at
        else:
            sub.tier_id = plan.tier_id
            sub.plan_id = _plan_id(plan)
            sub.is_on_trial = False
            sub.trial_ends_at = None
            sub.is_active = True
            sub.subscription_started_at = now
            sub.subscription_ends_at = ends_at

        sub.last_payment_id = _payment_id(payment)   # traceability
        db.add(sub)

    # ------------------------------------------------------------------
    # 8. Commit payment + subscription together
    # ------------------------------------------------------------------
    try:
        await _commit_or_rollback(
            db, operation="confirming a subscription payment"
        )
        await _refresh_or_raise(
            db, payment, operation="refreshing a payment"
        )
        await _refresh_or_raise(
            db, sub, operation="refreshing a subscription"
        )
    except Exception:
        raise

    logger.info(
        "Subscription payment confirmed: ref=%s firm_id=%s "
        "plan_id=%s service_id=%s ends_at=%s last_payment_id=%s",
        payment.payment_reference,
        payment.firm_id,
        payment.plan_id,
        service_id,
        ends_at,
        sub.last_payment_id,
    )

    return {
        "message": "Payment confirmed. Subscription is now active.",
        "payment": SubscriptionPaymentRead.model_validate(payment),
        "subscription": _compute_subscription_read(sub),
    }


# =============================================================================
# FIRM-SCOPED READS / UPDATES
# =============================================================================

async def read_subscriptions_for_firm(
    firm_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> list[SubscriptionRead]:
    guard_user_state(current_user)

    try:
        firm = await db.get(Firm, firm_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading the firm", exc) from exc

    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm with ID {firm_id} not found.",
        )
    if firm.user_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view subscriptions for your own firms.",
            headers={"X-Error-Code": "wrong_permission"},
        )

    try:
        result = await db.execute(
            select(Subscription)
            .where(col(Subscription.firm_id) == firm_id)
            .order_by(col(Subscription.service_id))
        )
        subs = result.scalars().all()
    except SQLAlchemyError as exc:
        raise _database_error("reading subscriptions", exc) from exc

    return [_compute_subscription_read(s) for s in subs]


async def read_subscription_status(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> SubscriptionStatusRead:
    guard_user_state(current_user)

    try:
        firm = await db.get(Firm, firm_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading the firm", exc) from exc

    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm with ID {firm_id} not found.",
        )
    if firm.user_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view subscriptions for your own firms.",
            headers={"X-Error-Code": "wrong_permission"},
        )

    try:
        service = await db.get(SubscriptionService, service_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading the service", exc) from exc

    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service with ID {service_id} not found.",
        )

    sub = await get_subscription_by_firm_service(db, firm_id, service_id)
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No subscription found for firm {firm_id} "
                f"on service '{service.name}'."
            ),
        )

    tier = (
        await get_tier_by_id(db, sub.tier_id) if sub.tier_id else None
    )
    plan = (
        await get_plan_by_id(db, sub.plan_id) if sub.plan_id else None
    )

    read = _compute_subscription_read(sub)

    return SubscriptionStatusRead(
        firm_id=firm_id,
        service_id=service_id,
        service_name=service.name,
        tier_name=tier.name if tier else None,
        plan_name=plan.name if plan else None,
        is_active=read.is_active,
        is_on_trial=read.is_on_trial,
        days_remaining=read.days_remaining or 0,
        status_label=read.status_label or "unknown",
        can_access=read.can_access,
    )


async def update_subscription(
    firm_id: int,
    service_id: int,
    data: SubscriptionUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    guard_user_state(current_user)

    try:
        firm = await db.get(Firm, firm_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading the firm", exc) from exc

    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm with ID {firm_id} not found.",
        )
    if firm.user_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own firms.",
            headers={"X-Error-Code": "wrong_permission"},
        )
    guard_firm_state(firm)

    sub = await get_subscription_by_firm_service(db, firm_id, service_id)
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No subscription found for firm {firm_id} "
                f"on service {service_id}."
            ),
        )

    if data.auto_renew is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )
    if data.auto_renew == sub.auto_renew:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No changes were made - auto_renew is already "
                f"{sub.auto_renew}."
            ),
        )

    sub.auto_renew = data.auto_renew
    db.add(sub)

    await _commit_or_rollback(db, operation="updating a subscription")
    await _refresh_or_raise(db, sub, operation="refreshing a subscription")

    logger.info(
        "Subscription auto_renew=%s firm_id=%s service_id=%s user_id=%s",
        sub.auto_renew,
        firm_id,
        service_id,
        current_user.id,
    )

    return {
        "message": (
            f"Auto-renew {'enabled' if sub.auto_renew else 'disabled'} "
            "successfully."
        ),
        "subscription": _compute_subscription_read(sub),
    }


# =============================================================================
# TRIAL — called from create_firm
# =============================================================================

async def create_trial_subscriptions_for_firm(
    db: AsyncSession,
    firm_id: int,
) -> None:
    """
    Create trial subscriptions for every active service for a new firm.
    Idempotent. Called from `create_firm`; not exposed via any route.
    """
    try:
        services_result = await db.execute(
            select(SubscriptionService).where(
                col(SubscriptionService.is_active).is_(True)
            )
        )
        services = services_result.scalars().all()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load services for trial creation firm_id=%s: %s",
            firm_id,
            exc,
        )
        return

    now = utc_now()

    for service in services:
        existing = await get_subscription_by_firm_service(
            db, firm_id, _service_id(service)
        )
        if existing:
            continue

        trial_ends_at = now + timedelta(days=service.trial_days)

        sub = Subscription(
            firm_id=firm_id,
            service_id=_service_id(service),
            is_on_trial=True,
            trial_started_at=now,
            trial_ends_at=trial_ends_at,
            is_active=True,
            auto_renew=False,
        )
        db.add(sub)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create trial subscriptions for firm_id=%s", firm_id
      )
