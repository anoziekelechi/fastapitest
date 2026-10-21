
from datetime import datetime
from decimal import Decimal
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from api.core.validators import (
    normalize_payment_option,
    normalize_user_message,
)










class TierWithPlansRead(BaseModel):
    """Tier with all its plans — for pricing pages."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_id: int
    name: str
    slug: str | None = None
    description: str | None = None
    is_active: bool
    plans: list["PlanRead"] = []

# my earlier version has this schema



# ============================================================
# SUBSCRIPTION ACTION SCHEMAS
# ============================================================




# ============================================================
# SUBSCRIPTION READ SCHEMAS
# ============================================================







# ============================================================
# RESPONSE ENVELOPES
# ============================================================



# helpers


from datetime import datetime, timezone
from math import ceil



def as_utc(value: datetime | None) -> datetime | None:
    """
    Ensure a datetime is timezone-aware.

    Naive datetimes are treated as UTC for compatibility with legacy records.
    Prefer storing timezone-aware timestamps in the database.
    """
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def days_until(
    expires_at: datetime | None,
    *,
    now: datetime | None = None,
) -> int | None:
    """Return remaining calendar-duration days, rounded up."""
    expiry = as_utc(expires_at)

    if expiry is None:
        return None

    current = as_utc(now or utc_now())
    assert current is not None

    seconds_remaining = (expiry - current).total_seconds()

    if seconds_remaining <= 0:
        return 0

    return ceil(seconds_remaining / 86400)


def is_unexpired(
    expires_at: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Check expiry using the actual timestamp, not rounded days."""
    expiry = as_utc(expires_at)

    if expiry is None:
        return False

    current = as_utc(now or utc_now())
    assert current is not None

    return expiry > current


def subscription_expiry(
    *,
    is_on_trial: bool,
    trial_ends_at: datetime | None,
    subscription_ends_at: datetime | None,
) -> datetime | None:
    """Return the relevant expiry timestamp for a subscription."""
    if is_on_trial:
        return trial_ends_at

    return subscription_ends_at


def get_subscription_status(
    *,
    is_active: bool,
    is_on_trial: bool,
    trial_ends_at: datetime | None,
    subscription_ends_at: datetime | None,
    now: datetime | None = None,
) -> tuple[int | None, str, bool]:
    """
    Return (days_remaining, status_label, can_access).

    An active trial requires a future trial expiry.
    A paid subscription requires a future subscription expiry.
    """
    expiry = subscription_expiry(
        is_on_trial=is_on_trial,
        trial_ends_at=trial_ends_at,
        subscription_ends_at=subscription_ends_at,
    )

    remaining = days_until(expiry, now=now)
    valid_expiry = is_unexpired(expiry, now=now)

    if not is_active:
        return remaining, "Inactive", False

    if not valid_expiry:
        if is_on_trial:
            return 0, "Trial expired", False
        return 0, "Expired", False

    if is_on_trial:
        return remaining, "Trial", True

    return remaining, "Active", True




# logics


import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.core.settings import get_settings
from api.core.slug import generate_slug
from api.models.subscriptions import (
    Subscription,
    SubscriptionPlan,
    SubscriptionService,
    SubscriptionTier,
)
from api.models.users import Firm
from api.subscriptions.helpers import (
    get_subscription_status,
    is_unexpired,
    utc_now,
)
from api.subscriptions.schemas import (
    ActivateSubscriptionResponse,
    CreatePlanResponse,
    CreateServiceResponse,
    CreateTierResponse,
    MessageResponse,
    PlanCreate,
    PlanRead,
    PlanUpdate,
    ServiceCreate,
    ServiceRead,
    ServiceUpdate,
    ServiceWithTiersRead,
    SubscriptionActivate,
    SubscriptionRead,
    SubscriptionStatusRead,
    SubscriptionUpdate,
    TierCreate,
    TierRead,
    TierUpdate,
    TierWithPlansRead,
    UpdatePlanResponse,
    UpdateServiceResponse,
    UpdateSubscriptionResponse,
    UpdateTierResponse,
)
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Common error handling and conversion helpers
# ---------------------------------------------------------------------------

def _http_error(
    code: int,
    message: str,
) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def _database_error(
    operation: str,
    exc: SQLAlchemyError,
) -> HTTPException:
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


def _subscription_id(sub: Subscription) -> int:
    if sub.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The subscription has no database ID.",
        )
    return sub.id


def to_plan_read(plan: SubscriptionPlan) -> PlanRead:
    """Build a PlanRead with currency supplied by application settings."""
    result = PlanRead.model_validate(plan)
    return result.model_copy(
        update={"currency": settings.currency_code}
    )


def _compute_subscription_read(
    sub: Subscription,
) -> SubscriptionRead:
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


def _require_admin(current_user: ReadUser) -> None:
    if not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required.",
        )


async def _require_firm_access(
    db: AsyncSession,
    *,
    firm_id: int,
    current_user: ReadUser,
) -> Firm:
    """
    Verify the firm exists and the caller owns it or is an administrator.

    This assumes ReadUser.id is the user's primary key and Firm has an
    owner/user relationship represented by user_id. If your Firm model uses
    a different ownership column, change this one helper.
    """
    try:
        firm = await db.get(Firm, firm_id)
    except SQLAlchemyError as exc:
        raise _database_error("checking firm access", exc) from exc

    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firm not found.",
        )

    if not _is_admin(current_user):
        owner_id = getattr(firm, "user_id", None)
        if owner_id is None or owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this firm.",
            )

    return firm


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

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
            "looking up a firm's subscription",
            exc,
        ) from exc


async def _count_subscriptions(
    db: AsyncSession,
    *,
    **filters: Any,
) -> int:
    try:
        query = select(func.count()).select_from(Subscription)

        for field_name, value in filters.items():
            query = query.where(
                getattr(Subscription, field_name) == value
            )

        result = await db.execute(query)
        return int(result.scalar_one())
    except SQLAlchemyError as exc:
        raise _database_error("counting subscriptions", exc) from exc


# ---------------------------------------------------------------------------
# Service CRUD
# ---------------------------------------------------------------------------

async def create_service(
    data: ServiceCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> CreateServiceResponse:
    _require_admin(current_user)

    try:
        existing = await db.execute(
            select(SubscriptionService).where(
                func.lower(SubscriptionService.name)
                == data.name.lower()
            )
        )
        if existing.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A subscription service with this name already exists.",
            )

        service = SubscriptionService(
            name=data.name,
            description=data.description,
            slug=generate_slug(data.name),
            trial_days=data.trial_days,
            is_active=True,
        )
        db.add(service)
        await _commit_or_rollback(db, operation="creating a service")
        await _refresh_or_raise(
            db, service, operation="refreshing a new service"
        )

        return CreateServiceResponse(
            message="Subscription service created successfully.",
            service=ServiceRead.model_validate(service),
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("creating a service", exc) from exc


async def read_services(
    db: AsyncSession,
    *,
    current_user: ReadUser | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[ServiceRead]:
    query = select(SubscriptionService).order_by(
        SubscriptionService.name
    )

    if not _is_admin(current_user):
        query = query.where(SubscriptionService.is_active.is_(True))

    try:
        result = await db.execute(query.offset(skip).limit(limit))
        return [
            ServiceRead.model_validate(item)
            for item in result.scalars().all()
        ]
    except SQLAlchemyError as exc:
        raise _database_error("reading subscription services", exc) from exc


async def read_service_by_slug(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceRead:
    service = await get_service_by_slug(db, slug)

    if not service.is_active and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    return ServiceRead.model_validate(service)


async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceWithTiersRead:
    service = await get_service_by_slug(db, slug)
    is_admin = _is_admin(current_user)

    if not service.is_active and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    tier_query = select(SubscriptionTier).where(
        SubscriptionTier.service_id == _service_id(service)
    )

    if not is_admin:
        tier_query = tier_query.where(
            SubscriptionTier.is_active.is_(True)
        )

    tier_query = tier_query.options(
        selectinload(SubscriptionTier.plans)
    ).order_by(SubscriptionTier.name)

    try:
        result = await db.execute(tier_query)
        tiers = result.scalars().unique().all()
    except SQLAlchemyError as exc:
        raise _database_error("reading service tiers", exc) from exc

    tier_reads: list[TierWithPlansRead] = []

    for tier in tiers:
        plans = [
            to_plan_read(plan)
            for plan in sorted(tier.plans, key=lambda p: p.name)
            if is_admin or plan.is_active
        ]

        tier_reads.append(
            TierWithPlansRead(
                **TierRead.model_validate(tier).model_dump(),
                plans=plans,
            )
        )

    return ServiceWithTiersRead(
        **ServiceRead.model_validate(service).model_dump(),
        tiers=tier_reads,
    )


async def update_service(
    slug: str,
    data: ServiceUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> UpdateServiceResponse:
    _require_admin(current_user)
    service = await get_service_by_slug(db, slug)

    changes = data.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided.",
        )

    changed = False

    for field_name, value in changes.items():
        if getattr(service, field_name) != value:
            setattr(service, field_name, value)
            changed = True

    if not changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made.",
        )

    await _commit_or_rollback(db, operation="updating a service")
    await _refresh_or_raise(db, service, operation="refreshing a service")

    return UpdateServiceResponse(
        message="Subscription service updated successfully.",
        service=ServiceRead.model_validate(service),
    )


async def delete_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> MessageResponse:
    _require_admin(current_user)
    service = await get_service_by_slug(db, slug)

    count = await _count_subscriptions(
        db,
        service_id=_service_id(service),
    )
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This service cannot be deleted because subscriptions reference it.",
        )

    try:
        await db.delete(service)
        await _commit_or_rollback(db, operation="deleting a service")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("deleting a service", exc) from exc

    logger.info(
        "Subscription service %s deleted by administrator %s",
        slug,
        current_user.id,
    )
    return MessageResponse(message="Subscription service deleted successfully.")


# ---------------------------------------------------------------------------
# Tier CRUD
# ---------------------------------------------------------------------------

async def create_tier(
    data: TierCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> CreateTierResponse:
    _require_admin(current_user)

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

        return CreateTierResponse(
            message="Subscription tier created successfully.",
            tier=TierRead.model_validate(tier),
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("creating a tier", exc) from exc


async def read_tiers_for_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> list[TierRead]:
    service = await get_service_by_slug(db, slug)
    is_admin = _is_admin(current_user)

    if not service.is_active and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    query = select(SubscriptionTier).where(
        SubscriptionTier.service_id == _service_id(service)
    )

    if not is_admin:
        query = query.where(SubscriptionTier.is_active.is_(True))

    query = query.order_by(SubscriptionTier.name)

    try:
        result = await db.execute(query)
        return [
            TierRead.model_validate(item)
            for item in result.scalars().all()
        ]
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

        service = await db.get(
            SubscriptionService,
            tier.service_id,
        )
        if service is None or not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription tier not found.",
            )

    return TierRead.model_validate(tier)


async def update_tier(
    tier_id: int,
    data: TierUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> UpdateTierResponse:
    _require_admin(current_user)
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

    return UpdateTierResponse(
        message="Subscription tier updated successfully.",
        tier=TierRead.model_validate(tier),
    )


async def delete_tier(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> MessageResponse:
    _require_admin(current_user)
    tier = await get_tier_by_id(db, tier_id)

    count = await _count_subscriptions(
        db,
        tier_id=_tier_id(tier),
    )
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This tier cannot be deleted because subscriptions reference it.",
        )

    try:
        await db.delete(tier)
        await _commit_or_rollback(db, operation="deleting a tier")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("deleting a tier", exc) from exc

    return MessageResponse(message="Subscription tier deleted successfully.")


# ---------------------------------------------------------------------------
# Plan CRUD
# ---------------------------------------------------------------------------

async def create_plan(
    data: PlanCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> CreatePlanResponse:
    _require_admin(current_user)

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

        return CreatePlanResponse(
            message="Subscription plan created successfully.",
            plan=to_plan_read(plan),
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("creating a plan", exc) from exc


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

        service = await db.get(
            SubscriptionService,
            tier.service_id,
        )
        if service is None or not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription tier not found.",
            )

    query = select(SubscriptionPlan).where(
        SubscriptionPlan.tier_id == _tier_id(tier)
    )

    if not is_admin:
        query = query.where(SubscriptionPlan.is_active.is_(True))

    query = query.order_by(SubscriptionPlan.duration_days)

    try:
        result = await db.execute(query)
        return [
            to_plan_read(plan)
            for plan in result.scalars().all()
        ]
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

        tier = await db.get(SubscriptionTier, plan.tier_id)
        if tier is None or not tier.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )

        service = await db.get(
            SubscriptionService,
            tier.service_id,
        )
        if service is None or not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )

    return to_plan_read(plan)


async def update_plan(
    plan_id: int,
    data: PlanUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> UpdatePlanResponse:
    _require_admin(current_user)
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

    return UpdatePlanResponse(
        message="Subscription plan updated successfully.",
        plan=to_plan_read(plan),
    )


async def delete_plan(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> MessageResponse:
    _require_admin(current_user)
    plan = await get_plan_by_id(db, plan_id)

    count = await _count_subscriptions(
        db,
        plan_id=_plan_id(plan),
    )
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This plan cannot be deleted because subscriptions reference it.",
        )

    try:
        await db.delete(plan)
        await _commit_or_rollback(db, operation="deleting a plan")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("deleting a plan", exc) from exc

    return MessageResponse(message="Subscription plan deleted successfully.")
