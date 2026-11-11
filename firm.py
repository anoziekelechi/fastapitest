async def start_trial_for_service(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Start a free trial for a firm on a specific service.

    Preconditions:
        - Firm is accessible and active.
        - Service exists and is active.
        - Firm has NEVER had a subscription for this service.
        - Service has an ACTIVE TRIAL TIER (is_trial_tier=True)
          with at least one active plan.

    The trial activates the STARTER TIER of the service — not
    Pro/Premium. Tiers above Starter are never trialable.
    """
    guard_user_state(current_user)

    firm = await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=True,
    )
    fid = firm_id_of(firm)

    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    try:
        service = await db.get(SubscriptionService, service_id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found or inactive.",
        )

    # ------------------------------------------------------------------
    # 2. Firm must not have tried this service before
    # ------------------------------------------------------------------
    existing = await get_subscription_by_firm_service(
        db, fid, service_id,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This firm has already used '{service.name}'. "
                f"Free trials are only available once per service."
            ),
        )

    # ------------------------------------------------------------------
    # 3. Service must have an active TRIAL tier with an active plan
    #
    #    Pro/Premium are not trialable — only tiers flagged
    #    `is_trial_tier=True` (typically the Starter tier).
    # ------------------------------------------------------------------
    try:
        trial_tier_plan_row = (
            await db.execute(
                select(
                    SubscriptionTier.id,
                    SubscriptionPlan.id,
                )
                .join(
                    SubscriptionPlan,
                    col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),
                )
                .where(
                    col(SubscriptionTier.service_id) == service_id,
                    col(SubscriptionTier.is_active).is_(True),
                    col(SubscriptionTier.is_trial_tier).is_(True),
                    col(SubscriptionPlan.is_active).is_(True),
                )
                .order_by(col(SubscriptionPlan.price).asc())
                .limit(1)
            )
        ).first()
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate service trial availability.",
        )

    if trial_tier_plan_row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{service.name}' is not currently available for "
                f"trial — no active trial tier."
            ),
        )

    trial_tier_id, trial_plan_id = trial_tier_plan_row

    # ------------------------------------------------------------------
    # 4. Create the trial subscription
    #
    #    The trial pins the STARTER tier (via trial_tier_id) and
    #    the entry-level plan, so the frontend can show what the
    #    user will pay after the trial ends.
    # ------------------------------------------------------------------
    now = utc_now()
    trial_ends_at = now + timedelta(days=service.trial_days)

    sub = Subscription(
        firm_id=fid,
        service_id=service_id,
        tier_id=trial_tier_id,       # Starter, not Pro/Premium
        plan_id=trial_plan_id,       # cheapest plan in the trial tier
        last_payment_id=None,
        is_on_trial=True,
        trial_started_at=now,
        trial_ends_at=trial_ends_at,
        is_active=True,
        subscription_started_at=None,
        subscription_ends_at=None,
        auto_renew=False,
    )

    db.add(sub)

    try:
        await db.flush()
    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after trial IntegrityError: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.warning(
            "Trial creation violated a database integrity constraint: "
            "firm_id=%s service_id=%s, rollback_succeeded=%s",
            fid, service_id, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This firm already has a subscription for "
                f"'{service.name}'."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after trial flush error: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.exception(
            "Database error while flushing trial: "
            "firm_id=%s service_id=%s, rollback_succeeded=%s",
            fid, service_id, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to start the trial. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.exception(
            "Trial commit failed or has an uncertain outcome: "
            "firm_id=%s service_id=%s", fid, service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The trial could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(sub)
    except Exception:
        logger.exception(
            "Failed to refresh subscription after trial: "
            "firm_id=%s service_id=%s", fid, service_id,
        )

    logger.info(
        "Trial started: firm_id=%s service_id=%s trial_tier_id=%s "
        "trial_plan_id=%s trial_ends_at=%s by user_id=%s",
        fid, service_id, trial_tier_id, trial_plan_id,
        trial_ends_at, current_user.id,
    )

    return {
        "message": (
            f"Free trial for '{service.name}' activated until "
            f"{trial_ends_at.strftime('%Y-%m-%d')}."
        ),
        "subscription": to_subscription_read(sub, now=now),
    }


async def read_available_trial_services(
    firm_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> TrialServiceListRead:
    """
    List services the firm can still start a trial for.

    A service is available when ALL of the following hold:
        - The service is active.
        - The firm has never had a subscription for it
          (trial or paid).
        - The service has at least one ACTIVE TRIAL TIER
          (is_trial_tier=True) with at least one ACTIVE plan.

    Pro / Premium tiers are not trialable — only the Starter tier
    is flagged as trialable, so services without an active trial
    tier are excluded entirely.
    """
    guard_user_state(current_user)

    await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=False,
    )

    # ------------------------------------------------------------------
    # 1. Service IDs the firm already has a subscription for
    # ------------------------------------------------------------------
    try:
        used_rows = await db.execute(
            select(Subscription.service_id)
            .where(Subscription.firm_id == firm_id)
        )
        used_service_ids = {row[0] for row in used_rows.all()}
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load used services for firm_id=%s", firm_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load trial services. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 2. Buyable trial: service has an active trial tier with an
    #    active plan. Pro/Premium tiers are ignored entirely.
    # ------------------------------------------------------------------
    buyable_trial_plan = (
        select(SubscriptionPlan.id)
        .join(
            SubscriptionTier,
            col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),
        )
        .where(
            col(SubscriptionTier.service_id)
            == col(SubscriptionService.id),
            col(SubscriptionTier.is_active).is_(True),
            col(SubscriptionTier.is_trial_tier).is_(True),
            col(SubscriptionPlan.is_active).is_(True),
        )
        .correlate(SubscriptionService)
        .exists()
    )

    try:
        result = await db.execute(
            select(SubscriptionService)
            .where(
                col(SubscriptionService.is_active).is_(True),
                buyable_trial_plan,
            )
            .order_by(col(SubscriptionService.name).asc())
        )
        services = list(result.scalars().all())
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load services for trial listing: firm_id=%s",
            firm_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load trial services. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 3. Filter out already-used services
    # ------------------------------------------------------------------
    available = [
        s for s in services
        if service_id_of(s) not in used_service_ids
    ]

    # ------------------------------------------------------------------
    # 4. Cheapest TRIAL-TIER plan per service (for "from $X")
    #
    # Price shown is the entry price a user would pay once the
    # trial ends — pulled only from the trial tier's plans, since
    # Pro/Premium aren't part of the trial offer.
    # ------------------------------------------------------------------
    service_ids = [service_id_of(s) for s in available]
    cheapest: dict[int, float] = {}

    if service_ids:
        try:
            rows = await db.execute(
                select(
                    SubscriptionTier.service_id,
                    func.min(SubscriptionPlan.price),
                )
                .join(
                    SubscriptionPlan,
                    col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),
                )
                .where(
                    col(SubscriptionTier.service_id).in_(service_ids),
                    col(SubscriptionTier.is_active).is_(True),
                    col(SubscriptionTier.is_trial_tier).is_(True),
                    col(SubscriptionPlan.is_active).is_(True),
                )
                .group_by(SubscriptionTier.service_id)
            )
            cheapest = {row[0]: row[1] for row in rows.all()}
        except SQLAlchemyError:
            logger.exception(
                "Failed to compute cheapest trial plans: firm_id=%s",
                firm_id,
            )
            # Non-fatal — proceed without price_from

    # ------------------------------------------------------------------
    # 5. Assemble
    # ------------------------------------------------------------------
    items = [
        TrialServiceRead(
            service_id=service_id_of(s),
            service_name=s.name,
            service_slug=s.slug or s.name,
            service_logo_url=resolve_logo_url(s.service_logo_key),
            description=s.description,
            trial_days=s.trial_days,
            price_from=cheapest.get(service_id_of(s)),
        )
        for s in available
    ]

    return TrialServiceListRead(
        firm_id=firm_id,
        total=len(items),
        services=items,
    )



"""Subscription routes."""

from fastapi import APIRouter, status

from api.deps import AdminUser, CurrentUser, DBDep
from api.subscriptions.logics.subscription_ops import (
    activate_subscription,
    read_available_trial_services,
    read_subscription_status,
    read_subscriptions_for_firm,
    start_trial_for_service,
    update_subscription,
)
from api.subscriptions.schemas import (
    ActivateSubscriptionResponse,
    StartTrialResponse,
    SubscriptionActivate,
    SubscriptionListRead,
    SubscriptionRead,
    SubscriptionStatusRead,
    SubscriptionUpdate,
    TrialServiceListRead,
    TrialStartRequest,
    UpdateSubscriptionResponse,
)


router = APIRouter(tags=["Subscriptions"])


# =============================================================================
# TRIAL — available services for this firm
# =============================================================================

@router.get(
    "/firms/{firm_id}/trials/available",
    response_model=TrialServiceListRead,
    summary="Services the firm can still trial",
)
async def list_trial_services(
    firm_id: int,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_available_trial_services(
        firm_id=firm_id, db=db, current_user=current_user,
    )


# =============================================================================
# TRIAL — start a trial for one service
# =============================================================================

@router.post(
    "/firms/{firm_id}/trials",
    response_model=StartTrialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a free trial for one service",
)
async def start_trial(
    firm_id: int,
    data: TrialStartRequest,
    db: DBDep,
    current_user: CurrentUser,
):
    return await start_trial_for_service(
        firm_id=firm_id,
        service_id=data.service_id,
        db=db,
        current_user=current_user,
    )


# =============================================================================
# READ — all subscriptions for a firm
# =============================================================================

@router.get(
    "/firms/{firm_id}/subscriptions",
    response_model=list[SubscriptionRead],
    summary="List a firm's subscriptions",
)
async def list_subscriptions(
    firm_id: int,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_subscriptions_for_firm(
        firm_id=firm_id, db=db, current_user=current_user,
    )


# =============================================================================
# READ — status for a specific (firm, service)
# =============================================================================

@router.get(
    "/firms/{firm_id}/subscriptions/{service_id}/status",
    response_model=SubscriptionStatusRead,
    summary="Subscription status for a firm and service",
)
async def status_for_service(
    firm_id: int,
    service_id: int,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_subscription_status(
        firm_id=firm_id,
        service_id=service_id,
        db=db,
        current_user=current_user,
    )


# =============================================================================
# UPDATE — auto_renew
# =============================================================================

@router.patch(
    "/firms/{firm_id}/subscriptions/{service_id}",
    response_model=UpdateSubscriptionResponse,
    summary="Update auto-renew on a subscription",
)
async def update(
    firm_id: int,
    service_id: int,
    data: SubscriptionUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_subscription(
        firm_id=firm_id,
        service_id=service_id,
        data=data,
        db=db,
        current_user=current_user,
    )


# =============================================================================
# ADMIN — activate / extend without payment
# =============================================================================

@router.post(
    "/admin/subscriptions/activate",
    response_model=ActivateSubscriptionResponse,
    summary="Activate a subscription without payment (admin)",
)
async def activate(
    data: SubscriptionActivate,
    db: DBDep,
    current_user: AdminUser,
):
    return await activate_subscription(
        data=data, db=db, current_user=current_user,
    )





# only select those logics not above

"""Subscription operations: read, update, trial, activate."""

import logging
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from api.core.media import resolve_logo_url
from api.firms.logics import firm_id_of, require_firm_access
from api.models.firm import Firm
from api.models.subscription import (
    Subscription,
    SubscriptionPlan,
    SubscriptionService,
    SubscriptionTier,
)
from api.core.guards import guard_firm_state, guard_user_state
from api.subscriptions.logics.subscription import (
    as_utc,
    compute_status_label,
    to_subscription_list,
    to_subscription_read,
    utc_now,
)
from api.subscriptions.schemas import (
    SubscriptionActivate,
    SubscriptionRead,
    SubscriptionStatusRead,
    SubscriptionUpdate,
    TrialServiceListRead,
    TrialServiceRead,
)
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)


# =============================================================================
# ID NARROWING
# =============================================================================

def subscription_id_of(sub: Subscription) -> int:
    if sub.id is None:
        logger.error(
            "Subscription row has no id — data integrity issue",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load subscription. Please try again.",
        )
    return sub.id


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

async def get_subscription_by_firm_service(
    db: AsyncSession,
    firm_id: int,
    service_id: int,
) -> Subscription | None:
    """
    Fetch the subscription row for a (firm, service) pair.

    Assumes `subscriptions` has a unique constraint on
    `(firm_id, service_id)` — one subscription per firm/service.
    """
    try:
        result = await db.execute(
            select(Subscription).where(
                Subscription.firm_id == firm_id,
                Subscription.service_id == service_id,
            )
        )
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch subscription firm_id=%s service_id=%s",
            firm_id, service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load subscription. Please try again.",
        ) from exc


async def get_tier_by_id(
    db: AsyncSession,
    tier_id: int,
) -> SubscriptionTier | None:
    try:
        return await db.get(SubscriptionTier, tier_id)
    except SQLAlchemyError as exc:
        logger.exception("Failed to fetch tier id=%s", tier_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load tier. Please try again.",
        ) from exc


async def get_plan_by_id(
    db: AsyncSession,
    plan_id: int,
) -> SubscriptionPlan | None:
    try:
        return await db.get(SubscriptionPlan, plan_id)
    except SQLAlchemyError as exc:
        logger.exception("Failed to fetch plan id=%s", plan_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plan. Please try again.",
        ) from exc


# =============================================================================
# READ — subscriptions for a firm
# =============================================================================

async def read_subscriptions_for_firm(
    firm_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> list[SubscriptionRead]:
    """
    List all subscriptions for a firm.

    Returns derived fields (`status_label`, `days_remaining`, etc.)
    computed once for the whole list against a shared `now`.
    """
    guard_user_state(current_user)

    await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=False,
    )

    try:
        result = await db.execute(
            select(Subscription)
            .where(Subscription.firm_id == firm_id)
            .order_by(Subscription.service_id.asc())
        )
        subs = list(result.scalars().all())
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load subscriptions firm_id=%s", firm_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load subscriptions. Please try again.",
        ) from exc

    return to_subscription_list(subs)


# =============================================================================
# READ — status for a specific (firm, service)
# =============================================================================

async def read_subscription_status(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> SubscriptionStatusRead:
    """Compact status for the 'is my firm subscribed?' widget."""
    guard_user_state(current_user)

    await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=False,
    )

    try:
        service = await db.get(SubscriptionService, service_id)
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load service id=%s", service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    sub = await get_subscription_by_firm_service(
        db, firm_id, service_id,
    )
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found for this firm and service.",
        )

    tier = (
        await get_tier_by_id(db, sub.tier_id)
        if sub.tier_id is not None else None
    )
    plan = (
        await get_plan_by_id(db, sub.plan_id)
        if sub.plan_id is not None else None
    )

    now = utc_now()
    status_label, can_access = compute_status_label(
        is_on_trial=sub.is_on_trial,
        is_active=sub.is_active,
        trial_ends_at=sub.trial_ends_at,
        subscription_ends_at=sub.subscription_ends_at,
        now=now,
    )
    read = to_subscription_read(sub, now=now)

    return SubscriptionStatusRead(
        firm_id=firm_id,
        service_id=service_id,
        service_name=service.name,
        tier_name=tier.name if tier else None,
        plan_name=plan.name if plan else None,
        is_active=read.is_active,
        is_on_trial=read.is_on_trial,
        days_remaining=read.days_remaining or 0,
        status_label=read.status_label,
        can_access=can_access,
    )


# =============================================================================
# UPDATE — auto_renew
# =============================================================================

async def update_subscription(
    firm_id: int,
    service_id: int,
    data: SubscriptionUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Toggle auto-renew on a subscription."""
    guard_user_state(current_user)

    firm = await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=True,
    )
    fid = firm_id_of(firm)

    sub = await get_subscription_by_firm_service(
        db, fid, service_id,
    )
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found for this firm and service.",
        )

    if data.auto_renew == sub.auto_renew:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No changes were made; auto_renew is already "
                f"{sub.auto_renew}."
            ),
        )

    sub.auto_renew = data.auto_renew
    db.add(sub)

    try:
        await db.flush()
    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after subscription flush error: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.exception(
            "Database error while flushing subscription update: "
            "firm_id=%s service_id=%s, rollback_succeeded=%s",
            fid, service_id, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update subscription. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.exception(
            "Subscription commit failed or has an uncertain outcome: "
            "firm_id=%s service_id=%s", fid, service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The subscription update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(sub)
    except Exception:
        logger.exception(
            "Failed to refresh subscription after update: "
            "firm_id=%s service_id=%s", fid, service_id,
        )

    logger.info(
        "Subscription auto_renew=%s firm_id=%s service_id=%s user_id=%s",
        sub.auto_renew, fid, service_id, current_user.id,
    )

    return {
        "message": (
            f"Auto-renew "
            f"{'enabled' if sub.auto_renew else 'disabled'} "
            "successfully."
        ),
        "subscription": to_subscription_read(sub),
    }


# =============================================================================
# TRIAL — list available services for this firm
# =============================================================================

async def read_available_trial_services(
    firm_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> TrialServiceListRead:
    """
    List services the firm can still start a trial for.

    A service is available when:
        - it is active,
        - the firm has never had a subscription for it
          (trial or paid),
        - it has at least one active tier with at least one active plan.

    Services the firm has already trialled (or paid for) are
    excluded — the frontend list shrinks as the firm consumes them.
    """
    guard_user_state(current_user)

    await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=False,
    )

    # ------------------------------------------------------------------
    # 1. Service IDs the firm already has a subscription for
    # ------------------------------------------------------------------
    try:
        used_rows = await db.execute(
            select(Subscription.service_id)
            .where(Subscription.firm_id == firm_id)
        )
        used_service_ids = {row[0] for row in used_rows.all()}
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load used services for firm_id=%s", firm_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load trial services. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 2. Active services with a buyable tier/plan
    # ------------------------------------------------------------------
    buyable_plan = (
        select(SubscriptionPlan.id)
        .join(
            SubscriptionTier,
            col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),
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

    try:
        result = await db.execute(
            select(SubscriptionService)
            .where(
                col(SubscriptionService.is_active).is_(True),
                buyable_plan,
            )
            .order_by(col(SubscriptionService.name).asc())
        )
        services = list(result.scalars().all())
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load services for trial listing: firm_id=%s",
            firm_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load trial services. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 3. Filter out already-used services
    # ------------------------------------------------------------------
    available = [
        s for s in services
        if service_id_of(s) not in used_service_ids
    ]

    # ------------------------------------------------------------------
    # 4. Cheapest plan per service (for "from $X")
    # ------------------------------------------------------------------
    service_ids = [service_id_of(s) for s in available]
    cheapest: dict[int, float] = {}

    if service_ids:
        try:
            rows = await db.execute(
                select(
                    SubscriptionTier.service_id,
                    func.min(SubscriptionPlan.price),
                )
                .join(
                    SubscriptionPlan,
                    col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),
                )
                .where(
                    col(SubscriptionTier.service_id).in_(service_ids),
                    col(SubscriptionTier.is_active).is_(True),
                    col(SubscriptionPlan.is_active).is_(True),
                )
                .group_by(SubscriptionTier.service_id)
            )
            cheapest = {row[0]: row[1] for row in rows.all()}
        except SQLAlchemyError:
            logger.exception(
                "Failed to compute cheapest plans: firm_id=%s",
                firm_id,
            )
            # Non-fatal — proceed without price_from

    # ------------------------------------------------------------------
    # 5. Assemble
    # ------------------------------------------------------------------
    items = [
        TrialServiceRead(
            service_id=service_id_of(s),
            service_name=s.name,
            service_slug=s.slug or s.name,
            service_logo_url=resolve_logo_url(s.service_logo_key),
            description=s.description,
            trial_days=s.trial_days,
            price_from=cheapest.get(service_id_of(s)),
        )
        for s in available
    ]

    return TrialServiceListRead(
        firm_id=firm_id,
        total=len(items),
        services=items,
    )


# =============================================================================
# TRIAL — start trial for one service
# =============================================================================

async def start_trial_for_service(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Start a one-month (service.trial_days) free trial for a firm
    on a specific service.

    Preconditions:
        - Firm is accessible and active.
        - Service exists, is active, and has a buyable tier/plan.
        - Firm has NEVER had a subscription for this service
          (trial or paid).
    """
    guard_user_state(current_user)

    firm = await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=True,
    )
    fid = firm_id_of(firm)

    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    try:
        service = await db.get(SubscriptionService, service_id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found or inactive.",
        )

    # ------------------------------------------------------------------
    # 2. Firm must not have tried this service before
    # ------------------------------------------------------------------
    existing = await get_subscription_by_firm_service(
        db, fid, service_id,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This firm has already used '{service.name}'. "
                f"Free trials are only available once per service."
            ),
        )

    # ------------------------------------------------------------------
    # 3. Service must be buyable (has active tier + active plan)
    # ------------------------------------------------------------------
    try:
        buyable_plan_id = (
            await db.execute(
                select(SubscriptionPlan.id)
                .join(
                    SubscriptionTier,
                    col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),
                )
                .where(
                    col(SubscriptionTier.service_id) == service_id,
                    col(SubscriptionTier.is_active).is_(True),
                    col(SubscriptionPlan.is_active).is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate service availability.",
        )

    if buyable_plan_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{service.name}' is not currently available "
                f"for trial — no active plan."
            ),
        )

    # ------------------------------------------------------------------
    # 4. Create the trial subscription
    # ------------------------------------------------------------------
    now = utc_now()
    trial_ends_at = now + timedelta(days=service.trial_days)

    sub = Subscription(
        firm_id=fid,
        service_id=service_id,
        tier_id=None,
        plan_id=None,
        last_payment_id=None,
        is_on_trial=True,
        trial_started_at=now,
        trial_ends_at=trial_ends_at,
        is_active=True,
        subscription_started_at=None,
        subscription_ends_at=None,
        auto_renew=False,
    )

    db.add(sub)

    try:
        await db.flush()
    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after trial IntegrityError: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.warning(
            "Trial creation violated a database integrity constraint: "
            "firm_id=%s service_id=%s, rollback_succeeded=%s",
            fid, service_id, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This firm already has a subscription for "
                f"'{service.name}'."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after trial flush error: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.exception(
            "Database error while flushing trial: "
            "firm_id=%s service_id=%s, rollback_succeeded=%s",
            fid, service_id, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to start the trial. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.exception(
            "Trial commit failed or has an uncertain outcome: "
            "firm_id=%s service_id=%s", fid, service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The trial could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(sub)
    except Exception:
        logger.exception(
            "Failed to refresh subscription after trial: "
            "firm_id=%s service_id=%s", fid, service_id,
        )

    logger.info(
        "Trial started: firm_id=%s service_id=%s trial_ends_at=%s "
        "by user_id=%s",
        fid, service_id, trial_ends_at, current_user.id,
    )

    return {
        "message": (
            f"Free trial for '{service.name}' activated until "
            f"{trial_ends_at.strftime('%Y-%m-%d')}."
        ),
        "subscription": to_subscription_read(sub, now=now),
    }


# =============================================================================
# ADMIN — activate / extend without payment
# =============================================================================

async def activate_subscription(
    data: SubscriptionActivate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Admin override: activate or extend a subscription without payment.

    Idempotent extension: if the firm is already on the exact same
    plan and it is not expired, extend from the current end date.
    Otherwise start a fresh window from now.
    """
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )

    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 1. Resolve firm + service
    # ------------------------------------------------------------------
    try:
        firm = await db.get(Firm, data.firm_id)
        service = await db.get(SubscriptionService, data.service_id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load activation resources.",
        ) from exc

    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firm not found.",
        )
    fid = firm_id_of(firm)
    guard_firm_state(firm)

    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found or inactive.",
        )
    sid = service_id_of(service)

    # ------------------------------------------------------------------
    # 2. Validate tier + plan belong to the service
    # ------------------------------------------------------------------
    tier = await get_tier_by_id(db, data.tier_id)
    if tier is None or not tier.is_active or tier.service_id != sid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The selected tier does not belong to the specified "
                "service or is inactive."
            ),
        )

    plan = await get_plan_by_id(db, data.plan_id)
    if plan is None or not plan.is_active or plan.tier_id != tier.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The selected plan does not belong to the specified "
                "tier or is inactive."
            ),
        )

    # ------------------------------------------------------------------
    # 3. Load existing sub (if any)
    # ------------------------------------------------------------------
    now = utc_now()
    sub = await get_subscription_by_firm_service(db, fid, sid)

    same_plan = (
        sub is not None
        and sub.tier_id == tier.id
        and sub.plan_id == plan.id
        and not sub.is_on_trial
        and sub.is_active
        and sub.subscription_ends_at is not None
        and as_utc(sub.subscription_ends_at) > now
    )

    if same_plan:
        assert sub is not None
        assert sub.subscription_ends_at is not None
        base_time = as_utc(sub.subscription_ends_at)
    else:
        base_time = now

    # Extend by calendar months (your existing helper)
    ends_at = add_calendar_months(base_time, plan.duration_months)

    # ------------------------------------------------------------------
    # 4. Create or update
    # ------------------------------------------------------------------
    if sub is None:
        sub = Subscription(
            firm_id=fid,
            service_id=sid,
            tier_id=tier.id,
            plan_id=plan.id,
            last_payment_id=None,
            is_on_trial=False,
            trial_started_at=None,
            trial_ends_at=None,
            is_active=True,
            subscription_started_at=now,
            subscription_ends_at=ends_at,
            auto_renew=False,
        )
        db.add(sub)
    else:
        sub.tier_id = tier.id
        sub.plan_id = plan.id
        sub.is_on_trial = False
        sub.trial_started_at = None
        sub.trial_ends_at = None
        sub.is_active = True
        sub.subscription_ends_at = ends_at
        sub.auto_renew = False
        if not same_plan:
            sub.subscription_started_at = now
        db.add(sub)

    # ------------------------------------------------------------------
    # 5. Flush → commit → refresh
    # ------------------------------------------------------------------
    try:
        await db.flush()
    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after activation flush error: "
                "firm_id=%s service_id=%s", fid, sid,
            )
        logger.exception(
            "Database error while flushing activation: "
            "firm_id=%s service_id=%s", fid, sid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to activate subscription. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "firm_id=%s service_id=%s", fid, sid,
            )
        logger.exception(
            "Activation commit failed or has an uncertain outcome: "
            "firm_id=%s service_id=%s", fid, sid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The activation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(sub)
    except Exception:
        logger.exception(
            "Failed to refresh subscription after activation: "
            "firm_id=%s service_id=%s", fid, sid,
        )

    logger.info(
        "Admin activated subscription: firm_id=%s service_id=%s "
        "tier_id=%s plan_id=%s by user_id=%s",
        fid, sid, tier.id, plan.id, current_user.id,
    )

    return {
        "message": (
            f"Subscription activated for '{service.name}' until "
            f"{ends_at.strftime('%Y-%m-%d')}."
        ),
        "subscription": to_subscription_read(sub, now=now),
                          }






"""Subscription builders and status computation."""

from datetime import datetime, timedelta, timezone

from api.models.subscription import Subscription
from api.subscriptions.schemas import (
    SubscriptionRead,
    SubscriptionStatusLabel,
)


# =============================================================================
# TIME HELPERS
# =============================================================================

def utc_now() -> datetime:
    """Timezone-aware UTC now — the single source of 'current time'."""
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """Normalize a datetime to UTC (aware if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_status_label(
    *,
    is_on_trial: bool,
    is_active: bool,
    trial_ends_at: datetime | None,
    subscription_ends_at: datetime | None,
    now: datetime,
) -> tuple[SubscriptionStatusLabel, bool]:
    """
    Return (status_label, can_access).

    Precedence:
        1. Inactive → "inactive", False.
        2. On trial:
             a. trial in the future → "trial", True.
             b. trial ended → "trial_expired", False.
        3. Paid:
             a. ends_at in the future → "active", True.
             b. ends_at in the past or missing → "expired", False.
        4. No signals → "unknown", False.
    """
    if not is_active:
        return ("inactive", False)

    if is_on_trial:
        if trial_ends_at is None:
            return ("unknown", False)
        if as_utc(trial_ends_at) > now:
            return ("trial", True)
        return ("trial_expired", False)

    if subscription_ends_at is None:
        # Paid subscription with no end date is a data bug — treat
        # as expired rather than silently granting access.
        return ("expired", False)

    if as_utc(subscription_ends_at) > now:
        return ("active", True)

    return ("expired", False)


def compute_days_remaining(
    *,
    is_on_trial: bool,
    trial_ends_at: datetime | None,
    subscription_ends_at: datetime | None,
    now: datetime,
) -> int | None:
    """
    Days remaining until the current phase ends.

    Returns 0 when the end date has already passed; None when there
    is no end date to measure from.
    """
    ends_at: datetime | None = None
    if is_on_trial and trial_ends_at is not None:
        ends_at = as_utc(trial_ends_at)
    elif not is_on_trial and subscription_ends_at is not None:
        ends_at = as_utc(subscription_ends_at)

    if ends_at is None:
        return None

    delta = ends_at - now
    # Ceiling: if 0 < delta < 1 day, report 1 day left.
    if delta.total_seconds() <= 0:
        return 0
    return max(0, (delta.days + (1 if delta.seconds else 0)))


# =============================================================================
# BUILDER — pure, no DB, no HTTP
# =============================================================================

def to_subscription_read(
    sub: Subscription,
    *,
    now: datetime | None = None,
) -> SubscriptionRead:
    """
    Assemble a SubscriptionRead with all derived fields resolved.

    This is the ONLY place that computes `is_active`, `is_on_trial`,
    `days_remaining`, `status_label`, and `can_access`. Every read
    endpoint funnels through here so they can never disagree.
    """
    if now is None:
        now = utc_now()

    read = SubscriptionRead.model_validate(sub)

    status_label, can_access = compute_status_label(
        is_on_trial=sub.is_on_trial,
        is_active=sub.is_active,
        trial_ends_at=sub.trial_ends_at,
        subscription_ends_at=sub.subscription_ends_at,
        now=now,
    )

    read.is_active = sub.is_active
    read.is_on_trial = sub.is_on_trial
    read.days_remaining = compute_days_remaining(
        is_on_trial=sub.is_on_trial,
        trial_ends_at=sub.trial_ends_at,
        subscription_ends_at=sub.subscription_ends_at,
        now=now,
    )
    read.status_label = status_label
    read.can_access = can_access
    return read


def to_subscription_list(
    subs: list[Subscription] | tuple[Subscription, ...],
    *,
    now: datetime | None = None,
) -> list[SubscriptionRead]:
    """Assemble a list of SubscriptionRead with a shared 'now'."""
    if now is None:
        now = utc_now()
    return [to_subscription_read(s, now=now) for s in subs]


# =============================================================================
# Backward-compat aliases
# =============================================================================

# Your existing code calls `compute_subscription_read(...)`. Keep it
# as an alias so you don't have to touch every call site at once.
compute_subscription_read = to_subscription_read





"""Subscription schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)


# =============================================================================
# SUBSCRIPTION — READ
# =============================================================================

SubscriptionStatusLabel = Literal[
    "active",
    "trial",
    "trial_expired",
    "expired",
    "inactive",
    "unknown",
]


class SubscriptionRead(BaseModel):
    """
    A single subscription as returned to the frontend.

    Derived fields (`is_active`, `is_on_trial`, `days_remaining`,
    `status_label`, `can_access`) are computed by the builder from
    the raw row and the current time — the caller never assembles
    them inline.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    service_id: int
    tier_id: int | None = None
    plan_id: int | None = None
    last_payment_id: int | None = None

    is_on_trial: bool
    trial_started_at: datetime | None = None
    trial_ends_at: datetime | None = None

    subscription_started_at: datetime | None = None
    subscription_ends_at: datetime | None = None

    auto_renew: bool

    # Derived
    is_active: bool
    days_remaining: int | None = None
    status_label: SubscriptionStatusLabel = "unknown"
    can_access: bool

    created_at: datetime
    updated_at: datetime


class SubscriptionStatusRead(BaseModel):
    """
    Compact status payload for the "is my firm subscribed?" widget.
    """
    firm_id: int
    service_id: int
    service_name: str
    tier_name: str | None = None
    plan_name: str | None = None
    is_active: bool
    is_on_trial: bool
    days_remaining: int
    status_label: SubscriptionStatusLabel = "unknown"
    can_access: bool


# =============================================================================
# SUBSCRIPTION — UPDATE
# =============================================================================

class SubscriptionUpdate(BaseModel):
    """Toggle auto-renew."""
    model_config = ConfigDict(extra="forbid")

    auto_renew: bool

    @model_validator(mode="after")
    def reject_explicit_null(self):
        if (
            "auto_renew" in self.model_fields_set
            and self.auto_renew is None
        ):
            raise ValueError("auto_renew cannot be null.")
        return self


# =============================================================================
# TRIAL — SELECTION FLOW
# =============================================================================

class TrialServiceRead(BaseModel):
    """
    A service the firm can still start a trial for.

    Returned by `GET /firms/{firm_id}/subscriptions/trials/available`.
    Services already trialled by this firm are excluded.
    """
    service_id: int
    service_name: str
    service_slug: str
    service_logo_url: str | None = None
    description: str | None = None
    trial_days: int
    price_from: Decimal | None = None  # cheapest active plan price


class TrialServiceListRead(BaseModel):
    firm_id: int
    total: int
    services: list[TrialServiceRead]


class TrialStartRequest(BaseModel):
    """Start a trial for one service."""
    model_config = ConfigDict(extra="forbid")

    service_id: int


# =============================================================================
# ADMIN ACTIVATION
# =============================================================================

class SubscriptionActivate(BaseModel):
    """
    Admin override: activate/extend a subscription without payment.
    """
    model_config = ConfigDict(extra="forbid")

    firm_id: int
    service_id: int
    tier_id: int
    plan_id: int


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class SubscriptionListRead(BaseModel):
    firm_id: int
    total: int
    subscriptions: list[SubscriptionRead]


class UpdateSubscriptionResponse(BaseModel):
    message: str
    subscription: SubscriptionRead


class StartTrialResponse(BaseModel):
    message: str
    subscription: SubscriptionRead


class ActivateSubscriptionResponse(BaseModel):
    message: str
    subscription: SubscriptionRead







