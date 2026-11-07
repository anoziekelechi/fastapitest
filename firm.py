


"""Subscription plan schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from api.core.validators import normalize_field


# =============================================================================
# PLAN — REQUEST
# =============================================================================

ALLOWED_SUBSCRIPTION_DURATIONS = (1, 2, 3, 6, 12)


class PlanCreate(BaseModel):
    """
    Create a plan under a tier.

    `tier_id` is NOT here — the parent tier is resolved from the URL
    (`POST /services/{svc}/tiers/{tier}/plans`). The service and tier
    are part of the resource address.
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(None, max_length=2000)
    duration_months: int = Field(..., gt=0)
    price: Decimal = Field(..., gt=0, max_digits=10, decimal_places=2)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        return normalize_field(v)

    @field_validator("duration_months")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v not in ALLOWED_SUBSCRIPTION_DURATIONS:
            raise ValueError(
                f"Duration must be one of "
                f"{', '.join(str(d) for d in ALLOWED_SUBSCRIPTION_DURATIONS)} months."
            )
        return v


class PlanUpdate(BaseModel):
    """
    Update a plan.

    Mutable:
        - description
        - duration_months
        - price
        - is_active

    Immutable:
        - name        (URL and receipts key on it)
        - slug        (derived from tier + name at creation)
        - tier_id     (a plan never moves between tiers)
    """
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(None, max_length=2000)
    duration_months: int | None = Field(None, gt=0)
    price: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2)
    is_active: bool | None = None

    @field_validator("duration_months")
    @classmethod
    def validate_duration(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if v not in ALLOWED_SUBSCRIPTION_DURATIONS:
            raise ValueError(
                f"Duration must be one of "
                f"{', '.join(str(d) for d in ALLOWED_SUBSCRIPTION_DURATIONS)} months."
            )
        return v

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        for field in ("description", "duration_months", "price", "is_active"):
            if (
                field in self.model_fields_set
                and getattr(self, field) is None
            ):
                raise ValueError(f"{field} cannot be null.")
        return self


# =============================================================================
# PLAN — READ
# =============================================================================

class PlanRead(BaseModel):
    """Full plan shape."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    tier_id: int
    name: str
    description: str | None = None
    slug: str
    duration_months: int
    price: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("price")
    def serialize_price(self, v: Decimal) -> str:
        return f"{v:.2f}"


class PlanSummaryRead(BaseModel):
    """
    Compact plan row used inside tier-detail responses.

    Also reused by `read_tier_by_slug` — keep this in sync with
    whatever fields that view needs.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    duration_months: int
    price: Decimal
    is_active: bool

    @field_serializer("price")
    def serialize_price(self, v: Decimal) -> str:
        return f"{v:.2f}"


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreatePlanResponse(BaseModel):
    message: str
    plan: PlanRead


class UpdatePlanResponse(BaseModel):
    message: str
    plan: PlanRead


class DeletePlanResponse(BaseModel):
    message: str
    plan_name: str





"""Subscription plan business logic."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from api.core.slug import generate_slug
from api.models.subscription import (
    Subscription,
    SubscriptionPlan,
    SubscriptionService,
    SubscriptionTier,
)
from api.subscriptions.schemas import (
    ALLOWED_SUBSCRIPTION_DURATIONS,
    PlanCreate,
    PlanRead,
    PlanSummaryRead,
    PlanUpdate,
)
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)


# =============================================================================
# ID NARROWING
# =============================================================================

def plan_id_of(plan: SubscriptionPlan) -> int:
    if plan.id is None:
        logger.error(
            "SubscriptionPlan row has no id — data integrity issue",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plan. Please try again.",
        )
    return plan.id


def tier_id_of(tier: SubscriptionTier) -> int:
    if tier.id is None:
        logger.error(
            "SubscriptionTier row has no id — data integrity issue",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load tier. Please try again.",
        )
    return tier.id


def service_id_of(service: SubscriptionService) -> int:
    if service.id is None:
        logger.error(
            "SubscriptionService row has no id — data integrity issue",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        )
    return service.id


# =============================================================================
# LOADERS
# =============================================================================

async def get_service_by_slug(
    db: AsyncSession,
    slug: str,
) -> SubscriptionService:
    """Fetch a service by slug or raise 404."""
    normalized_slug = slug.strip().lower()
    try:
        result = await db.execute(
            select(SubscriptionService).where(
                SubscriptionService.slug == normalized_slug,
            )
        )
        service = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch service slug=%s", normalized_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{slug}' not found.",
        )
    return service


async def get_tier_by_id(
    db: AsyncSession,
    tier_id: int,
) -> SubscriptionTier:
    """Fetch a tier by numeric id or raise 404."""
    try:
        tier = await db.get(SubscriptionTier, tier_id)
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch tier id=%s", tier_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load tier. Please try again.",
        ) from exc

    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tier id={tier_id} not found.",
        )
    return tier


async def get_tier_by_service_and_slug(
    db: AsyncSession,
    service_id: int,
    tier_slug: str,
) -> SubscriptionTier:
    """Fetch a tier by (service_id, slug) or raise 404."""
    normalized_slug = tier_slug.strip().lower()
    try:
        result = await db.execute(
            select(SubscriptionTier).where(
                SubscriptionTier.service_id == service_id,
                SubscriptionTier.slug == normalized_slug,
            )
        )
        tier = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch tier service_id=%s slug=%s",
            service_id, normalized_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load tier. Please try again.",
        ) from exc

    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tier '{tier_slug}' not found for this service.",
        )
    return tier


async def get_plan_by_slug(
    db: AsyncSession,
    slug: str,
    *,
    must_exist: bool = True,
) -> SubscriptionPlan | None:
    """
    Fetch a plan by slug (globally unique).

    Plan slugs are prefixed by tier slug at creation
    (`{tier.slug}-{plan.name}`), and tier slugs are prefixed by
    service name — so plan slugs are globally unique by construction.
    """
    normalized_slug = slug.strip().lower()
    try:
        result = await db.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.slug == normalized_slug,
            )
        )
        plan = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch plan slug=%s", normalized_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plan. Please try again.",
        ) from exc

    if plan is None and must_exist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan '{slug}' not found.",
        )
    return plan


async def get_plan_by_tier_and_slug(
    db: AsyncSession,
    tier_id: int,
    plan_slug: str,
) -> SubscriptionPlan:
    """Fetch a plan by (tier_id, slug) — used by scoped routes."""
    normalized_slug = plan_slug.strip().lower()
    try:
        result = await db.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.tier_id == tier_id,
                SubscriptionPlan.slug == normalized_slug,
            )
        )
        plan = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch plan tier_id=%s slug=%s",
            tier_id, normalized_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plan. Please try again.",
        ) from exc

    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan '{plan_slug}' not found for this tier.",
        )
    return plan


async def get_plan_by_tier_and_name(
    db: AsyncSession,
    tier_id: int,
    name: str,
) -> SubscriptionPlan | None:
    """Fetch a plan by (tier_id, name) — for uniqueness pre-checks."""
    try:
        result = await db.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.tier_id == tier_id,
                SubscriptionPlan.name == name,
            )
        )
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch plan tier_id=%s name=%s", tier_id, name,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plan. Please try again.",
        ) from exc


async def count_subscriptions_for_plan(
    db: AsyncSession,
    plan_id: int,
) -> int:
    """Count subscriptions attached to a plan (FK guard for delete)."""
    try:
        result = await db.execute(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.plan_id == plan_id)
        )
        return result.scalar() or 0
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to count subscriptions for plan_id=%s", plan_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plan. Please try again.",
        ) from exc


# =============================================================================
# BUILDERS — pure, no DB, no HTTP
# =============================================================================

def to_plan_read(plan: SubscriptionPlan) -> PlanRead:
    """Assemble a full PlanRead from a SubscriptionPlan row."""
    return PlanRead.model_validate(plan)


def to_plan_summary_read(plan: SubscriptionPlan) -> PlanSummaryRead:
    """Assemble a compact PlanSummaryRead (for nested tier views)."""
    return PlanSummaryRead.model_validate(plan)


def to_plan_list(
    plans: list[SubscriptionPlan] | tuple[SubscriptionPlan, ...],
) -> list[PlanRead]:
    """Assemble a list of PlanRead."""
    return [to_plan_read(p) for p in plans]


# =============================================================================
# CREATE
# =============================================================================

async def create_plan(
    service_slug: str,
    tier_slug: str,
    data: PlanCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a plan under a tier.

    URL: POST /services/{service_slug}/tiers/{tier_slug}/plans

    - The service and tier must exist (404 otherwise).
    - The tier must be active — you cannot add plans to a disabled
      tier.
    - `(tier_id, name)` must be unique per tier.
    - Slug is `{tier.slug}-{plan.name}` — globally unique by
      construction (tier slugs are already globally unique).
    - `duration_months` is constrained to the allowed set.
    """

    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    # ------------------------------------------------------------------
    # 2. Resolve the tier within the service
    # ------------------------------------------------------------------
    tier = await get_tier_by_service_and_slug(
        db, service_id=sid, tier_slug=tier_slug,
    )
    tid = tier_id_of(tier)

    # ------------------------------------------------------------------
    # 3. Tier must be active
    # ------------------------------------------------------------------
    if not tier.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot create a plan for an inactive tier.",
        )

    # ------------------------------------------------------------------
    # 4. Pre-check: (tier_id, name) uniqueness
    # ------------------------------------------------------------------
    existing = await get_plan_by_tier_and_name(
        db, tier_id=tid, name=data.name,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A plan named '{data.name}' already exists "
                f"for this tier."
            ),
        )

    # ------------------------------------------------------------------
    # 5. Prepare the model
    # ------------------------------------------------------------------
    plan = SubscriptionPlan(
        tier_id=tid,
        name=data.name,
        description=data.description,
        duration_months=data.duration_months,
        price=data.price,
        slug=generate_slug(f"{tier.slug or tier.name}-{data.name}"),
        is_active=True,
    )

    db.add(plan)

    # ------------------------------------------------------------------
    # 6. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after plan IntegrityError: "
                "tier_id=%s, name=%s", tid, data.name,
            )

        logger.warning(
            "Plan creation violated a database integrity constraint: "
            "tier_id=%s, name=%s, rollback_succeeded=%s",
            tid, data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A plan named '{data.name}' (or its slug) "
                f"already exists."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after plan flush error: tier_id=%s",
                tid,
            )

        logger.exception(
            "Database error while flushing new plan: "
            "tier_id=%s, rollback_succeeded=%s",
            tid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create plan. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    except Exception as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected plan flush error: "
                "tier_id=%s", tid,
            )

        logger.exception(
            "Unexpected error while flushing new plan: "
            "tier_id=%s, rollback_succeeded=%s",
            tid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating plan.",
        ) from exc

    # ------------------------------------------------------------------
    # 7. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "tier_id=%s, name=%s", tid, data.name,
            )

        logger.exception(
            "Plan commit failed or has an uncertain outcome: "
            "tier_id=%s, name=%s", tid, data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The plan creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 8. Refresh (best-effort) + return
    # ------------------------------------------------------------------
    try:
        await db.refresh(plan)
    except Exception:
        logger.exception(
            "Failed to refresh plan after create: tier_id=%s", tid,
        )

    logger.info(
        "Plan '%s' (slug=%s) created for tier_id=%s by user_id=%s",
        plan.name, plan.slug, tid, current_user.id,
    )

    return {
        "message": "Subscription plan created successfully.",
        "plan": to_plan_read(plan),
    }


# =============================================================================
# READ — for a tier (scoped)
# =============================================================================

async def read_plans_for_tier(
    service_slug: str,
    tier_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> list[PlanRead]:
    """
    List all plans under a tier.

    URL: GET /services/{service_slug}/tiers/{tier_slug}/plans

    Includes inactive plans. Admin-only via route. Sorted by
    `duration_months`, then `name` (stable tiebreak).
    """
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    tier = await get_tier_by_service_and_slug(
        db, service_id=sid, tier_slug=tier_slug,
    )
    tid = tier_id_of(tier)

    try:
        result = await db.execute(
            select(SubscriptionPlan)
            .where(SubscriptionPlan.tier_id == tid)
            .order_by(
                col(SubscriptionPlan.duration_months).asc(),
                col(SubscriptionPlan.name).asc(),
            )
        )
        plans = list(result.scalars().all())
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load plans for tier_id=%s", tid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plans. Please try again.",
        ) from exc

    return to_plan_list(plans)


# =============================================================================
# READ — single by slug (flat)
# =============================================================================

async def read_plan_by_slug(
    slug: str,
    db: AsyncSession,
) -> PlanRead:
    """
    Read a single plan by slug (admin-only via route).

    URL: GET /plans/{plan_slug}

    Slug is globally unique (tier slug + plan name), so no
    ambiguity across tiers.
    """
    plan = await get_plan_by_slug(db, slug)
    assert plan is not None
    return to_plan_read(plan)


# =============================================================================
# UPDATE — scoped by (service, tier, plan)
# =============================================================================

async def update_plan(
    service_slug: str,
    tier_slug: str,
    plan_slug: str,
    data: PlanUpdate,
    db: AsyncSession,
) -> dict:
    """
    Update a plan under a tier.

    URL: PATCH /services/{svc}/tiers/{tier}/plans/{plan}

    Mutable:
        - description
        - duration_months
        - price
        - is_active

    Immutable:
        - name        (URL and receipts key on it)
        - slug        (derived at creation)
        - tier_id     (a plan never moves between tiers)

    Contract:
        - Empty payload rejected.
        - No-op rejected.
        - flush → commit → refresh.
        - No IntegrityError branch: name and slug are immutable here.
    """

    # ------------------------------------------------------------------
    # 1. Resolve the hierarchy
    # ------------------------------------------------------------------
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    tier = await get_tier_by_service_and_slug(
        db, service_id=sid, tier_slug=tier_slug,
    )
    tid = tier_id_of(tier)

    plan = await get_plan_by_tier_and_slug(
        db, tier_id=tid, plan_slug=plan_slug,
    )
    pid = plan_id_of(plan)

    # ==============================================================
    # 2. Reject completely empty update payload
    # ==============================================================
    if all(
        v is None
        for v in (
            data.description,
            data.duration_months,
            data.price,
            data.is_active,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ==============================================================
    # 3. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    if (
        data.description is not None
        and data.description != plan.description
    ):
        plan.description = data.description
        updated_fields.append("description")

    if (
        data.duration_months is not None
        and data.duration_months != plan.duration_months
    ):
        plan.duration_months = data.duration_months
        updated_fields.append("duration_months")

    if (
        data.price is not None
        and data.price != plan.price
    ):
        plan.price = data.price
        updated_fields.append("price")

    if (
        data.is_active is not None
        and data.is_active != plan.is_active
    ):
        plan.is_active = data.is_active
        updated_fields.append("is_active")

    # ==============================================================
    # 4. Reject no-op
    # ==============================================================
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ---------------------------------------------------------
    # 5. Flush
    # ---------------------------------------------------------
    try:
        await db.flush()

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after plan flush error: plan_id=%s",
                pid,
            )

        logger.exception(
            "Database error while flushing plan update: "
            "plan_id=%s, rollback_succeeded=%s",
            pid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update plan. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    except Exception as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error: "
                "plan_id=%s", pid,
            )

        logger.exception(
            "Unexpected error while flushing plan update: "
            "plan_id=%s, rollback_succeeded=%s",
            pid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating plan.",
        ) from exc

    # ---------------------------------------------------------
    # 6. Commit
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: plan_id=%s",
                pid,
            )

        logger.exception(
            "Plan commit failed or has an uncertain outcome: "
            "plan_id=%s", pid,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The plan update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 7. Refresh (best-effort) + return
    # ---------------------------------------------------------
    try:
        await db.refresh(plan)
    except Exception:
        logger.exception(
            "Failed to refresh plan after update: plan_id=%s", pid,
        )

    logger.info(
        "Plan updated successfully: plan_id=%s, fields=%s",
        pid, ",".join(updated_fields),
    )

    return {
        "message": "Subscription plan updated successfully.",
        "plan": to_plan_read(plan),
    }


# =============================================================================
# DELETE — scoped by (service, tier, plan)
# =============================================================================

async def delete_plan(
    service_slug: str,
    tier_slug: str,
    plan_slug: str,
    db: AsyncSession,
) -> dict[str, str]:
    """
    Delete a plan under a tier.

    URL: DELETE /services/{svc}/tiers/{tier}/plans/{plan}

    Refused if subscriptions reference it (FK RESTRICT). The
    friendly pre-check backs the FK against the concurrent-
    subscription race.
    """

    # ------------------------------------------------------------------
    # 1. Resolve the hierarchy
    # ------------------------------------------------------------------
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    tier = await get_tier_by_service_and_slug(
        db, service_id=sid, tier_slug=tier_slug,
    )
    tid = tier_id_of(tier)

    plan = await get_plan_by_tier_and_slug(
        db, tier_id=tid, plan_slug=plan_slug,
    )
    pid = plan_id_of(plan)
    plan_name = plan.name

    # ------------------------------------------------------------------
    # 2. Refuse if subscriptions reference it
    # ------------------------------------------------------------------
    count = await count_subscriptions_for_plan(db, pid)
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Plan '{plan_name}' cannot be deleted because "
                f"{count} "
                f"{'subscription references' if count == 1 else 'subscriptions reference'} "
                f"it."
            ),
        )

    # ------------------------------------------------------------------
    # 3. Stage the delete + flush
    # ------------------------------------------------------------------
    try:
        await db.delete(plan)
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after plan IntegrityError: plan_id=%s",
                pid,
            )

        logger.warning(
            "Plan delete blocked by FK constraint: plan_id=%s, "
            "rollback_succeeded=%s",
            pid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Plan '{plan_name}' now has subscriptions and "
                f"cannot be deleted. Refresh and try again."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after plan delete flush error: "
                "plan_id=%s", pid,
            )

        logger.exception(
            "Database error while flushing plan delete: "
            "plan_id=%s, rollback_succeeded=%s",
            pid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to delete plan. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    except Exception as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected delete flush error: "
                "plan_id=%s", pid,
            )

        logger.exception(
            "Unexpected error while flushing plan delete: "
            "plan_id=%s, rollback_succeeded=%s",
            pid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting plan.",
        ) from exc

    # ------------------------------------------------------------------
    # 4. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: plan_id=%s",
                pid,
            )

        logger.exception(
            "Plan delete commit failed or has an uncertain outcome: "
            "plan_id=%s", pid,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The plan deletion could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    logger.info("Plan '%s' (id=%s) deleted", plan_name, pid)

    return {
        "message": "Subscription plan deleted successfully.",
        "plan_name": plan_name,
        }




"""Subscription plan routes."""

from fastapi import APIRouter, status

from api.deps import AdminUser, DBDep
from api.subscriptions.logics.plan import (
    create_plan,
    delete_plan,
    read_plan_by_slug,
    read_plans_for_tier,
    update_plan,
)
from api.subscriptions.schemas import (
    CreatePlanResponse,
    DeletePlanResponse,
    PlanCreate,
    PlanRead,
    PlanUpdate,
    UpdatePlanResponse,
)


router = APIRouter(tags=["Subscription Plans"])


# =============================================================================
# CREATE — POST /services/{svc}/tiers/{tier}/plans
# =============================================================================

@router.post(
    "/services/{service_slug}/tiers/{tier_slug}/plans",
    response_model=CreatePlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a plan under a tier (admin)",
)
async def create(
    service_slug: str,
    tier_slug: str,
    data: PlanCreate,
    db: DBDep,
    current_user: AdminUser,
):
    return await create_plan(
        service_slug=service_slug,
        tier_slug=tier_slug,
        data=data,
        db=db,
        current_user=current_user,
    )


# =============================================================================
# READ — GET /services/{svc}/tiers/{tier}/plans
# =============================================================================

@router.get(
    "/services/{service_slug}/tiers/{tier_slug}/plans",
    response_model=list[PlanRead],
    summary="List plans for a tier (admin)",
)
async def list_for_tier(
    service_slug: str,
    tier_slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await read_plans_for_tier(
        service_slug=service_slug,
        tier_slug=tier_slug,
        db=db,
        current_user=current_user,
    )


# =============================================================================
# READ — GET /plans/{plan_slug}
# =============================================================================

@router.get(
    "/plans/{plan_slug}",
    response_model=PlanRead,
    summary="Get a plan by slug (admin)",
)
async def read_single(
    plan_slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await read_plan_by_slug(slug=plan_slug, db=db)


# =============================================================================
# UPDATE — PATCH /services/{svc}/tiers/{tier}/plans/{plan}
# =============================================================================

@router.patch(
    "/services/{service_slug}/tiers/{tier_slug}/plans/{plan_slug}",
    response_model=UpdatePlanResponse,
    summary="Update a plan (admin)",
)
async def update(
    service_slug: str,
    tier_slug: str,
    plan_slug: str,
    data: PlanUpdate,
    db: DBDep,
    current_user: AdminUser,
):
    return await update_plan(
        service_slug=service_slug,
        tier_slug=tier_slug,
        plan_slug=plan_slug,
        data=data,
        db=db,
    )


# =============================================================================
# DELETE — DELETE /services/{svc}/tiers/{tier}/plans/{plan}
# =============================================================================

@router.delete(
    "/services/{service_slug}/tiers/{tier_slug}/plans/{plan_slug}",
    response_model=DeletePlanResponse,
    summary="Delete a plan (admin)",
)
async def delete(
    service_slug: str,
    tier_slug: str,
    plan_slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await delete_plan(
        service_slug=service_slug,
        tier_slug=tier_slug,
        plan_slug=plan_slug,
        db=db,
)




# end plan
# =============================================================================
# TIER — READ (with plans)
# =============================================================================

class PlanSummaryRead(BaseModel):
    """
    Compact plan row for the tier-detail view.

    Fields beyond id/name/price are here so the frontend can render
    the exact shape shown in the spec:
        STARTER
            |-- 1 month  $5
            |-- 3 month  $10
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: Decimal
    currency: str | None = None
    duration_days: int | None = None
    is_active: bool


class TierWithPlansRead(TierRead):
    """
    A tier with its list of plans, sorted by name.

    Inherits all TierRead fields (id, name, description, is_active,
    slug, created_at, updated_at) and adds `plans`.
    """
    plans: list[PlanSummaryRead] = []



def to_tier_with_plans_read(
    tier: SubscriptionTier,
    plans: list[SubscriptionPlan] | tuple[SubscriptionPlan, ...],
) -> TierWithPlansRead:
    """
    Assemble a TierWithPlansRead.

    `plans` is provided by the caller (already filtered/sorted)
    so the builder stays pure — no DB access, no authorization.
    """
    read = TierWithPlansRead.model_validate(tier)
    read.plans = [
        PlanSummaryRead.model_validate(p)
        for p in plans
    ]
    return read


async def read_tier_by_slug(
    slug: str,
    db: AsyncSession,
) -> TierWithPlansRead:
    """
    Read a tier by slug with its list of plans (admin-only via route).

    - Includes inactive tiers, inactive plans, and tiers under
      inactive services.
    - Plans are sorted by name so the frontend gets a stable order
      without client-side sorting.
    """
    # ------------------------------------------------------------------
    # 1. Resolve the tier
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(SubscriptionTier).where(
                col(SubscriptionTier.slug) == slug,
            )
        )
        tier = result.scalars().first()
    except SQLAlchemyError as exc:
        raise database_error(
            "reading a subscription tier by slug", exc,
        ) from exc

    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription tier not found.",
        )

    tid = tier_id_of(tier)

    # ------------------------------------------------------------------
    # 2. Load plans for this tier (one query, sorted)
    # ------------------------------------------------------------------
    try:
        plans_result = await db.execute(
            select(SubscriptionPlan)
            .where(col(SubscriptionPlan.tier_id) == tid)
            .order_by(col(SubscriptionPlan.name).asc())
        )
        plans = list(plans_result.scalars().all())
    except SQLAlchemyError as exc:
        raise database_error(
            "reading plans for a subscription tier", exc,
        ) from exc

    # ------------------------------------------------------------------
    # 3. Assemble via builder
    # ------------------------------------------------------------------
    return to_tier_with_plans_read(tier, plans)




from api.subscriptions.schemas import TierWithPlansRead

@router.get(
    "/tiers/{slug}",
    response_model=TierWithPlansRead,
    summary="Get a tier with its plans (admin)",
)
async def read_single_tier(
    slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await read_tier_by_slug(slug=slug, db=db)







#helper
def tier_id_of(tier: SubscriptionTier) -> int:
    """Narrow SubscriptionTier.id to int or fail with a clean 500."""
    if tier.id is None:
        logger.error(
            "SubscriptionTier row has no id — data integrity issue",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load tier. Please try again.",
        )
    return tier.id






# =============================================================================
# BUILDERS — single source of truth for tier read assembly
# =============================================================================

def to_tier_read(tier: SubscriptionTier) -> TierRead:
    """
    Assemble a TierRead from a SubscriptionTier row.

    Currently a thin wrapper around model_validate. Kept as a named
    function so any future derived field (plan_count, price_range,
    subscription_count) lands here and nowhere else.
    """
    return TierRead.model_validate(tier)


def to_tier_list(
    tiers: list[SubscriptionTier] | tuple[SubscriptionTier, ...],
) -> list[TierRead]:
    """
    Assemble a list of TierRead.

    Returns a bare list — tiers are always nested under a service
    or scoped to one, so no `total` envelope is needed. If you ever
    expose a top-level "list all tiers" admin page, add a
    `TierListRead { total, tiers }` envelope then.
    """
    return [to_tier_read(t) for t in tiers]


# =============================================================================
# LOADERS — multi-table reads that a builder alone can't cover
# =============================================================================

async def load_tiers_for_service(
    db: AsyncSession,
    service: SubscriptionService,
) -> list[SubscriptionTier]:
    """
    Load all tiers (active + inactive) for a service, sorted by name.

    Used by admin tier-list endpoints. For public tier listing, add
    a filter param or a separate loader.
    """
    sid = service_id_of(service)
    try:
        result = await db.execute(
            select(SubscriptionTier)
            .where(col(SubscriptionTier.service_id) == sid)
            .order_by(col(SubscriptionTier.name).asc())
        )
        return list(result.scalars().all())
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load tiers for service_id=%s", sid,
        )
        raise database_error("reading service tiers", exc) from exc




# =============================================================================
# TIER — UPDATE
# =============================================================================

class TierUpdate(BaseModel):
    """
    Update a tier.

    Mutable:
        - description
        - is_active

    Immutable:
        - name        (URLs and plan references key on it)
        - slug        (server-managed; derived from service + name)
        - service_id  (a tier never moves between services)
    """
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(None, max_length=2000)
    is_active: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        for field in ("description", "is_active"):
            if (
                field in self.model_fields_set
                and getattr(self, field) is None
            ):
                raise ValueError(f"{field} cannot be null.")
        return self





# =============================================================================
# TIER — READ (with plans)
# =============================================================================

class PlanSummaryRead(BaseModel):
    """
    Compact plan row for the tier-detail view.

    Fields beyond id/name/price are here so the frontend can render
    the exact shape shown in the spec:
        STARTER
            |-- 1 month  $5
            |-- 3 month  $10
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: Decimal
    currency: str | None = None
    duration_days: int | None = None
    is_active: bool


class TierWithPlansRead(TierRead):
    """
    A tier with its list of plans, sorted by name.

    Inherits all TierRead fields (id, name, description, is_active,
    slug, created_at, updated_at) and adds `plans`.
    """
    plans: list[PlanSummaryRead] = []




async def get_tier_by_service_and_slug(
    db: AsyncSession,
    service_id: int,
    tier_slug: str,
    *,
    must_exist: bool = True,
) -> SubscriptionTier | None:
    """
    Fetch a tier by (service_id, slug).

    Scoped to a service so a tier slug like "starter" is resolved
    within the context of its parent service. Two services can each
    have a "starter" tier.

    `must_exist=True` (default) raises 404 when missing; the caller
    can pass False to get `None` and shape its own error.
    """
    normalized_slug = tier_slug.strip().lower()
    try:
        result = await db.execute(
            select(SubscriptionTier).where(
                col(SubscriptionTier.service_id) == service_id,
                col(SubscriptionTier.slug) == normalized_slug,
            )
        )
        tier = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch tier service_id=%s slug=%s",
            service_id, normalized_slug,
        )
        raise database_error(
            "reading a subscription tier", exc,
        ) from exc

    if tier is None and must_exist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tier '{tier_slug}' not found for this service.",
        )
    return tier



async def read_tier_with_plans(
    service_slug: str,
    tier_slug: str,
    db: AsyncSession,
) -> TierWithPlansRead:
    """
    Read a single tier (with plans) inside a service (admin-only).

    URL: GET /services/{service_slug}/tiers/{tier_slug}

    - Resolves the service first (404 if missing).
    - Resolves the tier inside the service (404 if it doesn't
      belong to that service, even if a tier with that slug exists
      under a different service).
    - Includes inactive tiers, inactive plans, and tiers under
      inactive services.
    - Plans are sorted by name.
    """
    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    # ------------------------------------------------------------------
    # 2. Resolve the tier within the service
    # ------------------------------------------------------------------
    tier = await get_tier_by_service_and_slug(
        db, service_id=sid, tier_slug=tier_slug,
    )
    assert tier is not None  # must_exist=True guarantees this
    tid = tier_id_of(tier)

    # ------------------------------------------------------------------
    # 3. Load plans
    # ------------------------------------------------------------------
    try:
        plans_result = await db.execute(
            select(SubscriptionPlan)
            .where(col(SubscriptionPlan.tier_id) == tid)
            .order_by(col(SubscriptionPlan.name).asc())
        )
        plans = list(plans_result.scalars().all())
    except SQLAlchemyError as exc:
        raise database_error(
            "reading plans for a subscription tier", exc,
        ) from exc

    # ------------------------------------------------------------------
    # 4. Assemble via builder
    # ------------------------------------------------------------------
    return to_tier_with_plans_read(tier, plans)



@router.get(
    "/services/{service_slug}/tiers/{tier_slug}",
    response_model=TierWithPlansRead,
    summary="Get a tier with its plans (admin)",
)
async def read_single_tier(
    service_slug: str,
    tier_slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await read_tier_with_plans(
        service_slug=service_slug,
        tier_slug=tier_slug,
        db=db,
    )




async def read_tiers_with_plans_for_service(
    service_slug: str,
    db: AsyncSession,
) -> ServiceWithTiersRead:
    """
    Read a service with all its tiers and plans (admin-only).

    URL: GET /services/{service_slug}/tiers

    Returns the full tree:
        Receipt
          ├─ Starter
          │    ├─ 1 month  $5
          │    └─ 3 month  $10
          └─ Pro
               └─ 1 month  $10
    """
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    # Load tiers with plans eager-loaded
    try:
        result = await db.execute(
            select(SubscriptionTier)
            .where(col(SubscriptionTier.service_id) == sid)
            .options(load_relationship(SubscriptionTier.plans))
            .order_by(col(SubscriptionTier.name).asc())
        )
        tiers = list(result.scalars().all())
    except SQLAlchemyError as exc:
        raise database_error(
            "reading service tiers", exc,
        ) from exc

    tier_reads = []
    for tier in tiers:
        plans = sorted(tier.plans or [], key=lambda p: p.name)
        tier_reads.append(to_tier_with_plans_read(tier, plans))

    # Reuse the ServiceWithTiersRead builder from earlier
    return to_service_with_tiers_read(service, tier_reads)



@router.get(
    "/services/{service_slug}/tiers",
    response_model=ServiceWithTiersRead,
    summary="List all tiers with plans for a service (admin)",
)
async def list_tiers_for_service(
    service_slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await read_tiers_with_plans_for_service(
        service_slug=service_slug, db=db,
    )


#latest
async def get_tier_by_service_and_slug(
    db: AsyncSession,
    service_id: int,
    tier_slug: str,
    *,
    must_exist: bool = True,
) -> SubscriptionTier | None:
    """Fetch a tier scoped to its parent service."""
    normalized_slug = tier_slug.strip().lower()
    try:
        result = await db.execute(
            select(SubscriptionTier).where(
                col(SubscriptionTier.service_id) == service_id,
                col(SubscriptionTier.slug) == normalized_slug,
            )
        )
        tier = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch tier service_id=%s slug=%s",
            service_id, normalized_slug,
        )
        raise database_error("reading a subscription tier", exc) from exc

    if tier is None and must_exist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tier '{tier_slug}' not found for this service.",
        )
    return tier



@router.get(
    "/services/{service_slug}/tiers",
    response_model=ServiceWithTiersRead,
    summary="List all tiers with plans for a service (admin)",
)
async def list_tiers(
    service_slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await read_tiers_with_plans_for_service(
        service_slug=service_slug, db=db,
    )


@router.get(
    "/services/{service_slug}/tiers/{tier_slug}",
    response_model=TierWithPlansRead,
    summary="Get one tier with its plans (admin)",
)
async def read_one_tier(
    service_slug: str,
    tier_slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await read_tier_with_plans(
        service_slug=service_slug,
        tier_slug=tier_slug,
        db=db,
    )




class TierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(None, max_length=2000)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        return normalize_field(v)  # your existing helper




class TierUpdate(BaseModel):
    """
    Update a tier.

    Mutable:
        - description
        - is_active

    Immutable:
        - name        (URLs and plan references key on it)
        - slug        (server-managed; derived from tier name)
        - service_id  (a tier never moves between services)
    """
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(None, max_length=2000)
    is_active: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        for field in ("description", "is_active"):
            if (
                field in self.model_fields_set
                and getattr(self, field) is None
            ):
                raise ValueError(f"{field} cannot be null.")
        return self




class TierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_id: int
    name: str
    description: str | None = None
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime



class CreateTierResponse(BaseModel):
    message: str
    tier: TierRead


class UpdateTierResponse(BaseModel):
    message: str
    tier: TierRead


class DeleteTierResponse(BaseModel):
    message: str
    tier_name: str



async def get_tier_by_service_and_slug(
    db: AsyncSession,
    service_id: int,
    tier_slug: str,
    *,
    must_exist: bool = True,
) -> SubscriptionTier | None:
    """
    Fetch a tier by (service_id, slug).

    Scoped to a service so a tier slug like "starter" is resolved
    within the context of its parent service. Two services can each
    have a "starter" tier.
    """
    normalized_slug = tier_slug.strip().lower()

    try:
        result = await db.execute(
            select(SubscriptionTier).where(
                col(SubscriptionTier.service_id) == service_id,
                col(SubscriptionTier.slug) == normalized_slug,
            )
        )
        tier = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch tier service_id=%s slug=%s",
            service_id, normalized_slug,
        )
        raise database_error(
            "reading a subscription tier", exc,
        ) from exc

    if tier is None and must_exist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tier '{tier_slug}' not found for this service.",
        )
    return tier



async def get_tier_by_service_and_name(
    db: AsyncSession,
    service_id: int,
    name: str,
    *,
    exclude_tier_id: int | None = None,
) -> SubscriptionTier | None:
    """Fetch a tier by (service_id, name), optionally excluding a specific id."""
    try:
        stmt = select(SubscriptionTier).where(
            col(SubscriptionTier.service_id) == service_id,
            col(SubscriptionTier.name) == name,
        )
        if exclude_tier_id is not None:
            stmt = stmt.where(col(SubscriptionTier.id) != exclude_tier_id)
        result = await db.execute(stmt)
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch tier service_id=%s name=%s",
            service_id, name,
        )
        raise database_error("reading a subscription tier", exc) from exc




async def create_tier(
    service_slug: str,
    data: TierCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a tier under a service.

    URL: POST /services/{service_slug}/tiers

    - The service must exist.
    - `(service_id, name)` must be unique — pre-checked, then
      enforced by the DB unique constraint for the race window.
    - `(service_id, slug)` must be unique — slug is generated from
      the tier name; if a same-named tier already exists, the
      pre-check has already rejected the request.
    - No file upload: no R2 cleanup phases.
    """

    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    # ------------------------------------------------------------------
    # 2. Pre-check: (service_id, name) uniqueness
    # ------------------------------------------------------------------
    existing = await get_tier_by_service_and_name(
        db, service_id=sid, name=data.name,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A tier named '{data.name}' already exists "
                f"for this service."
            ),
        )

    # ------------------------------------------------------------------
    # 3. Prepare the model
    #
    # The slug encodes the tier name only — the service lives in
    # the URL, so tiers across services can share the same slug.
    # ------------------------------------------------------------------
    tier = SubscriptionTier(
        service_id=sid,
        name=data.name,
        description=data.description,
        slug=generate_slug(data.name),
        is_active=True,
    )

    db.add(tier)

    # ------------------------------------------------------------------
    # 4. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after tier IntegrityError: "
                "service_id=%s, name=%s", sid, data.name,
            )

        logger.warning(
            "Tier creation violated a database integrity constraint: "
            "service_id=%s, name=%s, rollback_succeeded=%s",
            sid, data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A tier named '{data.name}' already exists "
                f"for this service."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after tier flush error: "
                "service_id=%s", sid,
            )

        logger.exception(
            "Database error while flushing new tier: "
            "service_id=%s, rollback_succeeded=%s",
            sid, rollback_succeeded,
        )

        raise database_error("creating a tier", exc) from exc

    except Exception as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected tier flush error: "
                "service_id=%s", sid,
            )

        logger.exception(
            "Unexpected error while flushing new tier: "
            "service_id=%s, rollback_succeeded=%s",
            sid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating tier.",
        ) from exc

    # ------------------------------------------------------------------
    # 5. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "service_id=%s, name=%s", sid, data.name,
            )

        logger.exception(
            "Tier commit failed or has an uncertain outcome: "
            "service_id=%s, name=%s", sid, data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The tier creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 6. Refresh (best-effort) + return
    # ------------------------------------------------------------------
    try:
        await db.refresh(tier)
    except Exception:
        logger.exception(
            "Failed to refresh tier after create: service_id=%s", sid,
        )

    logger.info(
        "Tier '%s' created for service_id=%s by admin user_id=%s",
        tier.name, sid, current_user.id,
    )

    return {
        "message": "Subscription tier created successfully.",
        "tier": to_tier_read(tier),
    }




async def update_tier(
    service_slug: str,
    tier_slug: str,
    data: TierUpdate,
    db: AsyncSession,
) -> dict:
    """
    Update a tier under a service.

    URL: PATCH /services/{service_slug}/tiers/{tier_slug}

    Mutable:
        - description
        - is_active

    Immutable:
        - name        (URLs and plan references key on it)
        - slug        (server-managed)
        - service_id  (a tier never moves between services)

    Contract — same as update_service / update_payment_method:
        - Empty payload rejected.
        - No-op rejected.
        - flush → commit → refresh.
        - No IntegrityError branch: no unique constraint can be
          violated (name and slug are immutable here).
    """

    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    # ------------------------------------------------------------------
    # 2. Resolve the tier within the service
    # ------------------------------------------------------------------
    tier = await get_tier_by_service_and_slug(
        db, service_id=sid, tier_slug=tier_slug,
    )
    assert tier is not None  # must_exist=True guarantees this
    tid = tier_id_of(tier)

    # ==============================================================
    # 3. Reject completely empty update payload
    # ==============================================================
    if all(
        v is None
        for v in (data.description, data.is_active)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ==============================================================
    # 4. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    # --- description ---
    if (
        data.description is not None
        and data.description != tier.description
    ):
        tier.description = data.description
        updated_fields.append("description")

    # --- is_active ---
    if (
        data.is_active is not None
        and data.is_active != tier.is_active
    ):
        tier.is_active = data.is_active
        updated_fields.append("is_active")

    # ==============================================================
    # 5. Reject no-op
    # ==============================================================
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ---------------------------------------------------------
    # 6. Flush
    #
    # No IntegrityError branch: name and slug are immutable here,
    # so no unique constraint can fire.
    # ---------------------------------------------------------
    try:
        await db.flush()

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after tier flush error: tier_id=%s",
                tid,
            )

        logger.exception(
            "Database error while flushing tier update: "
            "tier_id=%s, rollback_succeeded=%s",
            tid, rollback_succeeded,
        )

        raise database_error("updating a tier", exc) from exc

    except Exception as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected tier flush error: "
                "tier_id=%s", tid,
            )

        logger.exception(
            "Unexpected error while flushing tier update: "
            "tier_id=%s, rollback_succeeded=%s",
            tid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating tier.",
        ) from exc

    # ---------------------------------------------------------
    # 7. Commit
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: tier_id=%s",
                tid,
            )

        logger.exception(
            "Tier commit failed or has an uncertain outcome: "
            "tier_id=%s", tid,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The tier update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 8. Refresh (best-effort) + return
    # ---------------------------------------------------------
    try:
        await db.refresh(tier)
    except Exception:
        logger.exception(
            "Failed to refresh tier after update: tier_id=%s", tid,
        )

    logger.info(
        "Tier updated successfully: tier_id=%s, fields=%s",
        tid, ",".join(updated_fields),
    )

    return {
        "message": "Subscription tier updated successfully.",
        "tier": to_tier_read(tier),
    }



async def delete_tier(
    service_slug: str,
    tier_slug: str,
    db: AsyncSession,
) -> dict[str, str]:
    """
    Delete a tier under a service.

    URL: DELETE /services/{service_slug}/tiers/{tier_slug}

    - Refused if any subscription still references it (FK RESTRICT).
    - Friendly pre-check backs the FK against the concurrent-
      subscription race.
    - No file to clean up (tiers have no image).
    """

    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    # ------------------------------------------------------------------
    # 2. Resolve the tier within the service
    # ------------------------------------------------------------------
    tier = await get_tier_by_service_and_slug(
        db, service_id=sid, tier_slug=tier_slug,
    )
    assert tier is not None  # must_exist=True guarantees this
    tid = tier_id_of(tier)
    tier_name = tier.name

    # ------------------------------------------------------------------
    # 3. Refuse if subscriptions reference it
    # ------------------------------------------------------------------
    count = await count_subscriptions(db, tier_id=tid)
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Tier '{tier_name}' cannot be deleted because "
                f"{count} "
                f"{'subscription references' if count == 1 else 'subscriptions reference'} "
                f"it."
            ),
        )

    # ------------------------------------------------------------------
    # 4. Stage the delete + flush
    #
    # IntegrityError here = a subscription was inserted concurrently
    # after step 3. DB RESTRICT fires.
    # ------------------------------------------------------------------
    try:
        await db.delete(tier)
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after tier IntegrityError: tier_id=%s",
                tid,
            )

        logger.warning(
            "Tier delete blocked by FK constraint: tier_id=%s, "
            "rollback_succeeded=%s",
            tid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Tier '{tier_name}' now has subscriptions and "
                f"cannot be deleted. Refresh and try again."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after tier delete flush error: "
                "tier_id=%s", tid,
            )

        logger.exception(
            "Database error while flushing tier delete: "
            "tier_id=%s, rollback_succeeded=%s",
            tid, rollback_succeeded,
        )

        raise database_error("deleting a tier", exc) from exc

    except Exception as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected delete flush error: "
                "tier_id=%s", tid,
            )

        logger.exception(
            "Unexpected error while flushing tier delete: "
            "tier_id=%s, rollback_succeeded=%s",
            tid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting tier.",
        ) from exc

    # ------------------------------------------------------------------
    # 5. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: tier_id=%s",
                tid,
            )

        logger.exception(
            "Tier delete commit failed or has an uncertain outcome: "
            "tier_id=%s", tid,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The tier deletion could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    logger.info("Tier '%s' (id=%s) deleted", tier_name, tid)

    return {
        "message": "Subscription tier deleted successfully.",
        "tier_name": tier_name,
            }




from api.subscriptions.schemas import (
    CreateTierResponse,
    DeleteTierResponse,
    TierCreate,
    TierUpdate,
    UpdateTierResponse,
)


@router.post(
    "/services/{service_slug}/tiers",
    response_model=CreateTierResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tier under a service (admin)",
)
async def create(
    service_slug: str,
    data: TierCreate,
    db: DBDep,
    current_user: AdminUser,
):
    return await create_tier(
        service_slug=service_slug,
        data=data,
        db=db,
        current_user=current_user,
    )


@router.patch(
    "/services/{service_slug}/tiers/{tier_slug}",
    response_model=UpdateTierResponse,
    summary="Update a tier (admin)",
)
async def update(
    service_slug: str,
    tier_slug: str,
    data: TierUpdate,
    db: DBDep,
    current_user: AdminUser,
):
    return await update_tier(
        service_slug=service_slug,
        tier_slug=tier_slug,
        data=data,
        db=db,
    )


@router.delete(
    "/services/{service_slug}/tiers/{tier_slug}",
    response_model=DeleteTierResponse,
    summary="Delete a tier (admin)",
)
async def delete(
    service_slug: str,
    tier_slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await delete_tier(
        service_slug=service_slug,
        tier_slug=tier_slug,
        db=db,
    )








async def get_tier_by_service_and_slug(
    db: AsyncSession,
    service_id: int,
    tier_slug: str,
    *,
    must_exist: bool = True,
) -> SubscriptionTier | None:
    """
    Fetch a tier by (service_id, slug).

    Scoped to a service so a tier slug like "starter" is resolved
    within the context of its parent service. Two services can each
    have a "starter" tier.
    """
    normalized_slug = tier_slug.strip().lower()

    try:
        result = await db.execute(
            select(SubscriptionTier).where(
                col(SubscriptionTier.service_id) == service_id,
                col(SubscriptionTier.slug) == normalized_slug,
            )
        )
        tier = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch tier service_id=%s slug=%s",
            service_id, normalized_slug,
        )
        raise database_error(
            "reading a subscription tier", exc,
        ) from exc

    if tier is None and must_exist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tier '{tier_slug}' not found for this service.",
        )
    return tier















#old
# ---------------------------------------------------------------------------
# Service CRUD
# ---------------------------------------------------------------------------

async def create_service(
    data: ServiceCreate,
    service_image: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a service method.

    Admin access is enforced by the route's AdminUser dependency.

    service_image is optional,uploaded to R2 before the db write
    clean up if the DB fails
    """

    # ------------------------------------------------------------------
    # 1. Check for an existing payment method name
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(SubscriptionService).where(
                col(SubscriptionService.name) == data.name,
            
        )
        )
        existing = result.scalars().first()

    except SQLAlchemyError:
        await db.rollback()

        logger.exception(
            "Database error checking service method name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"sevice method '{data.name}' already exists.",
        )

    # ------------------------------------------------------------------
    # 2. Upload the optional image
    # ------------------------------------------------------------------
    new_image_key: str | None = None

    if service_image is not None:
        new_image_key = await handle_file_update(
            file=service_image,
            current_key=None,
            prefix=SERVICE_IMAGE,
            max_size=SERVICE_IMAGE_MAX_SIZE,
        )

    # ------------------------------------------------------------------
    # 3. Prepare the model
    # ------------------------------------------------------------------
    service = SubscriptionService(
        name=data.name,
        description=data.description,
        service_logo_key=new_image_key,
        trial_days=data.trial_days,
        is_active=True,
        slug=generate_slug(data.name),
    )

    # ------------------------------------------------------------------
    # 4. Best-effort cleanup for a failed database transaction
    # ------------------------------------------------------------------
    def cleanup_new_image() -> None:
        if new_image_key is None:
            return

        try:
            delete_from_r2(new_image_key)

        except Exception:
            logger.exception(
                "Failed to clean up service image in R2: key=%s",
                new_image_key,
            )

    # ------------------------------------------------------------------
    # 5. Persist the payment method
    # ------------------------------------------------------------------
    try:
        db.add(service)

        # Execute the INSERT and surface database constraints before commit.
        await db.flush()

        # Load database-generated values while the transaction is active.
        await db.refresh(service)

        # Commit only after the preceding operations succeed.
        await db.commit()

    except IntegrityError:
        await db.rollback()

        logger.warning(
            "service method creation violated a database integrity "
            "constraint: name=%s",
            data.name,
        )

        cleanup_new_image()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"service method '{data.name}' already exists "
                "or conflicts with a database constraint."
            ),
        )

    except SQLAlchemyError:
        await db.rollback()

        logger.exception(
            "Database error creating service method name=%s",
            data.name,
        )

        cleanup_new_image()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create service method. Please try again.",
        )

    except Exception:
        # Covers unexpected failures during persistence or refresh.
        # The transaction has not been committed if this block is reached.
        await db.rollback()

        logger.exception(
            "Unexpected error creating service method name=%s",
            data.name,
        )

        cleanup_new_image()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create service method. Please try again.",
        )

    # ------------------------------------------------------------------
    # 6. Log successful creation
    # ------------------------------------------------------------------
    logger.info(
        "service method '%s' (slug=%s) created by admin user_id=%s",
        service.name,
        service.slug,
        current_user.id,
    )

    # ------------------------------------------------------------------
    # 7. Return the response
    # ------------------------------------------------------------------
    return {
        "message": (
            f"Subscription service '{service.name}' created successfully."
        ),
        "service": to_service_read(service),
            }
    
async def read_services(
    db: AsyncSession,
    *,
    current_user: ReadUser | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[ServiceRead]:
    """
    List subscription services.

    Public users:
        - See active services only.
        - A service must have at least one active tier.
        - That tier must have at least one active plan.

    Administrators:
        - Can see all services, including inactive services.

    Args:
        db: Async database session.
        current_user: Authenticated user or None for public access.
        skip: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A list of ServiceRead objects.
    """
    admin = is_admin(current_user)

    query = (
        select(SubscriptionService)
        .order_by(col(SubscriptionService.name))
        .offset(skip)
        .limit(limit)
    )

    if not admin:
        # A service is buyable when an active tier contains
        # at least one active plan.
        buyable_exists = (
            select(SubscriptionTier.id)
            .join(
                SubscriptionPlan,
                col(SubscriptionPlan.tier_id)
                == col(SubscriptionTier.id),
            )
            .where(
                col(SubscriptionTier.service_id)
                == col(SubscriptionService.id),
                col(SubscriptionTier.is_active).is_(True),
                col(SubscriptionPlan.is_active).is_(True),
            )
            .correlate(SubscriptionService)
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
        raise database_error(
            "reading subscription services",
            exc,
        ) from exc

    return [
        to_service_read(service)
        for service in services
    ]

async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceWithTiersRead:
    """
    Read a subscription service with its tiers and plans.

    Public users:
        - Can access active services only.
        - Must have at least one active tier with an active plan.
        - See active tiers only.
        - See active plans only.

    Administrators:
        - Can access active and inactive services.
        - Can see active and inactive tiers.
        - Can see active and inactive plans.

    Args:
        slug: Subscription service slug.
        db: Async database session.
        current_user: Authenticated user or None for public access.

    Returns:
        ServiceWithTiersRead containing the service, tiers, and plans.
    """
    admin = is_admin(current_user)

    # 1. Retrieve the service.
    service = await get_service_by_slug(db, slug)

    # 2. Restrict public access to active, buyable services.
    if not admin:
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )

        buyable = await service_has_buyable(
            db,
            service_id(service),
        )

        if not buyable:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )

    # 3. Build the tier query.
    tier_query = (
        select(SubscriptionTier)
        .where(
            col(SubscriptionTier.service_id)
            == service_id(service)
        )
        .options(
            load_relationship(SubscriptionTier.plans)
        )
        .order_by(
            col(SubscriptionTier.name)
        )
    )

    # Public users can see active tiers only.
    if not admin:
        tier_query = tier_query.where(
            col(SubscriptionTier.is_active).is_(True)
        )

    # 4. Retrieve the tiers.
    try:
        result = await db.execute(tier_query)
        tiers = result.scalars().all()

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to read tiers for service slug=%s",
            slug,
        )

        raise database_error(
            "reading service tiers",
            exc,
        ) from exc

    # 5. Build the response tiers and filter their plans.
    tier_reads: list[TierWithPlansRead] = []

    for tier in tiers:
        plans = sorted(
            tier.plans or [],
            key=lambda plan: plan.name,
        )

        visible_plans = [
            plan
            for plan in plans
            if admin or plan.is_active
        ]

        tier_read = TierWithPlansRead.model_validate(
            tier
        ).model_copy(
            update={
                "plans": [
                    to_plan_read(plan)
                    for plan in visible_plans
                ]
            }
        )

        tier_reads.append(tier_read)

    # 6. Build the complete service response.
    return ServiceWithTiersRead(
        **ServiceRead.model_validate(
            service
        ).model_dump(),
        tiers=tier_reads,
        )



async def read_service_by_slug(
    slug: str,
    db: AsyncSession,
) -> ServiceRead:
    """
    Read a subscription service by slug.

    Administrators only.
    Includes inactive services.
    """

    return_service = await get_service_by_slug(db, slug)

    return to_service_read(return_service)




async def update_service(
    slug: str,
    data: ServiceUpdate,
    db: AsyncSession,
    service_image: UploadFile | None = None,
) -> dict:
    """
    Update a subscription service.

    - Rejects an empty payload.
    - Updates only fields that actually changed.
    - Uploads a replacement image before touching the DB record.
    - Deletes the new upload after a failed flush only if rollback
      succeeds.
    - Preserves the new upload if commit raises, because its outcome
      may be uncertain.
    - Deletes the old image only after a confirmed successful commit.

    No IntegrityError handling is needed — this endpoint cannot violate
    a unique constraint (name and slug are not editable here).
    """

    # ---------------------------------------------------------
    # 1. Retrieve the existing service
    # ---------------------------------------------------------
    service = await get_service_by_slug(db=db, slug=slug)

    # ==============================================================
    # 2. Reject completely empty update payload
    # ==============================================================
    if all(
        value is None
        for value in (
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

    # ---------------------------------------------------------
    # 3. Preserve the old image key
    # ---------------------------------------------------------
    old_image_key = service.service_logo_key
    new_image_key: str | None = None

    # ---------------------------------------------------------
    # 4. Upload a replacement image, if supplied
    # ---------------------------------------------------------
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
                "Service image upload failed: service_id=%s",
                service.id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload service image.",
            ) from exc

    # ==============================================================
    # 5. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    if data.description is not None:
        if data.description != service.description:
            service.description = data.description
            updated_fields.append("description")

    if data.trial_days is not None:
        if data.trial_days != service.trial_days:
            service.trial_days = data.trial_days
            updated_fields.append("trial_days")

    if data.is_active is not None:
        if data.is_active != service.is_active:
            service.is_active = data.is_active
            updated_fields.append("is_active")

    if new_image_key is not None:
        if new_image_key != service.service_logo_key:
            service.service_logo_key = new_image_key
            updated_fields.append("service_logo_key")

    # ==============================================================
    # 6. Reject if nothing actually changed
    # ==============================================================
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ---------------------------------------------------------
    # 7. Flush before attempting commit
    #
    # No IntegrityError branch — this update cannot violate a
    # unique constraint. SQLAlchemyError and Exception cover
    # the realistic failure modes.
    # ---------------------------------------------------------
    try:
        await db.flush()

    except SQLAlchemyError as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after service flush error: "
                "service_id=%s",
                service.id,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "failure: service_id=%s, image_key=%s",
                    service.id,
                    new_image_key,
                )

        logger.exception(
            "Database error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            service.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update service. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    except Exception as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error: "
                "service_id=%s",
                service.id,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "failure: service_id=%s, image_key=%s",
                    service.id,
                    new_image_key,
                )

        logger.exception(
            "Unexpected error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            service.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating service.",
        ) from exc

    # ---------------------------------------------------------
    # 8. Commit the transaction
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # IMPORTANT:
        # Do not delete new_image_key here.
        #
        # A commit exception does not prove the database rejected
        # the transaction. The commit may have succeeded while its
        # acknowledgement was lost.
        #
        # Preserve the uploaded image until the database state can
        # be verified and any orphaned object reconciled.

        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "service_id=%s",
                service.id,
            )

        logger.exception(
            "Service commit failed or has an uncertain outcome. "
            "Preserving replacement image for reconciliation: "
            "service_id=%s, new_image_key=%s",
            service.id,
            new_image_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 9. Commit succeeded: refresh and delete the old image
    # ---------------------------------------------------------
    try:
        await db.refresh(service)
    except Exception:
        # Refresh failure after commit is not fatal — the update
        # is already persisted. Log and continue.
        logger.exception(
            "Failed to refresh service after update: service_id=%s",
            service.id,
        )

    if (
        new_image_key is not None
        and old_image_key is not None
        and old_image_key != new_image_key
    ):
        try:
            delete_from_r2(old_image_key)

        except Exception:
            # The database update has already succeeded.
            # Do not report the update as failed because old-image
            # cleanup failed; log it for later reconciliation.
            logger.exception(
                "Service updated, but the old image could not be "
                "deleted: service_id=%s, old_image_key=%s",
                service.id,
                old_image_key,
            )

    # ---------------------------------------------------------
    # 10. Return the updated service
    # ---------------------------------------------------------
    logger.info(
        "Service updated successfully: service_id=%s, fields=%s",
        service.id,
        ",".join(updated_fields),
    )

    return {
        "message": "Service updated successfully.",
        "service": to_service_read(service),
        }


async def delete_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    require_admin(current_user)
    service = await get_service_by_slug(db, slug)

    count = await count_subscriptions(
        db,
        service_id=service_id(service),
    )
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This service cannot be deleted because subscriptions reference it.",
        )
    name=service.name
    try:
        await db.delete(service)
        await commit_or_rollback(db, operation="deleting a service")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise database_error("deleting a service", exc) from exc

    logger.info(
        "Subscription service %s deleted by administrator %s",
        slug,
        current_user.id,
    )
    return {"message":f"{name} Subscription service deleted successfully."}







