# api/subscriptions/logics.py (top of file, after existing imports)

from typing import Any
from sqlalchemy.orm import selectinload


def _load_relationship(attr: Any):
    """
    Wrap `selectinload` with a type-hiding shim.

    SQLModel declares relationships as their Python type
    (e.g. `list[SubscriptionPlan]`), but SQLAlchemy's metaclass
    replaces them with `InstrumentedAttribute` at runtime. Pyright
    sees the Python type and rejects the call. This helper keeps the
    suppression localized to one place instead of every call site.
    """
    return selectinload(attr)




from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col, select


async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceWithTiersRead:
    """
    Read a subscription service with its tiers and plans.

    Public callers see only active services, tiers, and plans.
    Admins can see inactive services, tiers, and plans.
    """
    service = await get_service_by_slug(db, slug)
    is_admin = _is_admin(current_user)

    if not service.is_active and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    tier_query = (
        select(SubscriptionTier)
        .where(
            col(SubscriptionTier.service_id)
            == _service_id(service)
        )
    )

    if not is_admin:
        tier_query = tier_query.where(
            col(SubscriptionTier.is_active).is_(True)
        )

    tier_query = (
        tier_query
        .options(
            _load_relationship(SubscriptionTier.plans)
        )
        .order_by(col(SubscriptionTier.name))
    )

    try:
        result = await db.execute(tier_query)
        tiers = result.scalars().all()

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to read tiers for service slug=%s",
            slug,
        )
        raise _database_error(
            "reading service tiers",
            exc,
        ) from exc

    tier_reads: list[TierWithPlansRead] = []

    for tier in tiers:
        plans = sorted(
            tier.plans or [],
            key=lambda plan: plan.name,
        )

        visible_plans = [
            plan
            for plan in plans
            if is_admin or plan.is_active
        ]

        tier_reads.append(
            TierWithPlansRead.model_validate(tier).model_copy(
                update={
                    "plans": [
                        to_plan_read(plan)
                        for plan in visible_plans
                    ],
                }
            )
        )

    return ServiceWithTiersRead(
        **ServiceRead.model_validate(service).model_dump(),
        tiers=tier_reads,
    )



#replace

async def _count_subscriptions(
    db: AsyncSession,
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
# Subscription activation
# ---------------------------------------------------------------------------

async def activate_subscription(
    data: SubscriptionActivate,
    db: AsyncSession,
    current_user: ReadUser,
) -> ActivateSubscriptionResponse:
    """
    Activate a paid subscription.

    Policy used here:
    - A new subscription starts immediately.
    - Renewing the same active plan extends from its existing future expiry.
    - Switching to a different plan starts the new plan from now.
    - Plan switching does not preserve the old plan's remaining time.
    - auto_renew is reset to False when activating or switching plans.

    If your payment workflow requires successful payment confirmation before
    activation, call this function only after that confirmation.
    """
    _require_admin(current_user)

    try:
        firm = await db.get(Firm, data.firm_id)
        service = await db.get(SubscriptionService, data.service_id)
        tier = await db.get(SubscriptionTier, data.tier_id)
        plan = await db.get(SubscriptionPlan, data.plan_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading activation records", exc) from exc

    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firm not found.",
        )

    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active subscription service not found.",
        )

    if tier is None or not tier.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active subscription tier not found.",
        )

    if tier.service_id != data.service_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected tier does not belong to this service.",
        )

    if plan is None or not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active subscription plan not found.",
        )

    if plan.tier_id != data.tier_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected plan does not belong to this tier.",
        )

    now = utc_now()

    try:
        # Lock the existing row if one exists. The unique constraint on
        # (firm_id, service_id) remains essential for concurrent requests.
        result = await db.execute(
            select(Subscription)
            .where(
                Subscription.firm_id == data.firm_id,
                Subscription.service_id == data.service_id,
            )
            .with_for_update()
        )
        sub = result.scalars().first()

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
                subscription_ends_at=now + timedelta(
                    days=plan.duration_days
                ),
                auto_renew=False,
            )
            db.add(sub)
        else:
            same_plan = (
                sub.plan_id == data.plan_id
                and not sub.is_on_trial
                and sub.is_active
                and is_unexpired(sub.subscription_ends_at, now=now)
            )

            if same_plan:
                # Extend from the existing expiry, but never from an
                # already-expired timestamp.
                base = sub.subscription_ends_at
                if base is None or not is_unexpired(base, now=now):
                    base = now

                sub.subscription_ends_at = base + timedelta(
                    days=plan.duration_days
                )
            else:
                # Explicit plan-switch policy: start the new plan now.
                sub.tier_id = data.tier_id
                sub.plan_id = data.plan_id
                sub.is_on_trial = False
                sub.trial_ends_at = None
                sub.is_active = True
                sub.subscription_started_at = now
                sub.subscription_ends_at = now + timedelta(
                    days=plan.duration_days
                )

            sub.tier_id = data.tier_id
            sub.plan_id = data.plan_id
            sub.is_on_trial = False
            sub.trial_ends_at = None
            sub.is_active = True
            sub.auto_renew = False

        await _commit_or_rollback(
            db,
            operation="activating a subscription",
        )
        await _refresh_or_raise(
            db,
            sub,
            operation="refreshing an activated subscription",
        )

        return ActivateSubscriptionResponse(
            message="Subscription activated successfully.",
            subscription=_compute_subscription_read(sub),
        )

    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("activating a subscription", exc) from exc


# ---------------------------------------------------------------------------
# Subscription reads
# ---------------------------------------------------------------------------

async def read_subscriptions_for_firm(
    firm_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> list[SubscriptionRead]:
    await _require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
    )

    try:
        result = await db.execute(
            select(Subscription)
            .where(Subscription.firm_id == firm_id)
            .order_by(Subscription.created_at.desc())
        )
        subscriptions = result.scalars().all()

        return [
            _compute_subscription_read(sub)
            for sub in subscriptions
        ]
    except SQLAlchemyError as exc:
        raise _database_error(
            "reading subscriptions for a firm",
            exc,
        ) from exc


async def read_subscription_status(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> SubscriptionStatusRead:
    await _require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
    )

    try:
        service = await db.get(SubscriptionService, service_id)
    except SQLAlchemyError as exc:
        raise _database_error(
            "loading subscription status service",
            exc,
        ) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    sub = await get_subscription_by_firm_service(
        db,
        firm_id,
        service_id,
    )

    if sub is None:
        return SubscriptionStatusRead(
            firm_id=firm_id,
            service_id=service_id,
            service_name=service.name,
            tier_name=None,
            plan_name=None,
            is_active=False,
            is_on_trial=False,
            days_remaining=0,
            status_label="Not subscribed",
            can_access=False,
        )

    tier_name: str | None = None
    plan_name: str | None = None

    try:
        if sub.tier_id is not None:
            tier = await db.get(SubscriptionTier, sub.tier_id)
            tier_name = tier.name if tier is not None else None

        if sub.plan_id is not None:
            plan = await db.get(SubscriptionPlan, sub.plan_id)
            plan_name = plan.name if plan is not None else None
    except SQLAlchemyError as exc:
        raise _database_error(
            "loading subscription status details",
            exc,
        ) from exc

    remaining, label, can_access = get_subscription_status(
        is_active=sub.is_active,
        is_on_trial=sub.is_on_trial,
        trial_ends_at=sub.trial_ends_at,
        subscription_ends_at=sub.subscription_ends_at,
    )

    return SubscriptionStatusRead(
        firm_id=firm_id,
        service_id=service_id,
        service_name=service.name,
        tier_name=tier_name,
        plan_name=plan_name,
        is_active=sub.is_active,
        is_on_trial=sub.is_on_trial,
        days_remaining=remaining,
        status_label=label,
        can_access=can_access,
    )


async def update_subscription(
    firm_id: int,
    service_id: int,
    data: SubscriptionUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> UpdateSubscriptionResponse:
    await _require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
    )

    sub = await get_subscription_by_firm_service(
        db,
        firm_id,
        service_id,
    )

    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found.",
        )

    if sub.auto_renew == data.auto_renew:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made.",
        )

    sub.auto_renew = data.auto_renew

    await _commit_or_rollback(
        db,
        operation="updating subscription settings",
    )
    await _refresh_or_raise(
        db,
        sub,
        operation="refreshing subscription settings",
    )

    return UpdateSubscriptionResponse(
        message="Subscription updated successfully.",
        subscription=_compute_subscription_read(sub),
    )


# ---------------------------------------------------------------------------
# Trial creation
# ---------------------------------------------------------------------------

async def create_trial_subscriptions_for_firm(
    firm_id: int,
    db: AsyncSession,
) -> list[Subscription]:
    """
    Add a trial subscription for each active service that the firm does not
    already have.

    This function intentionally does NOT commit. The caller, such as
    create_firm(), owns the transaction and should commit the firm and its
    trial subscriptions together.

    The unique constraint on (firm_id, service_id) is still required to
    protect against concurrent attempts.
    """
    try:
        firm = await db.get(Firm, firm_id)
        if firm is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Firm not found.",
            )

        result = await db.execute(
            select(SubscriptionService).where(
                SubscriptionService.is_active.is_(True)
            )
        )
        services = result.scalars().all()

        existing_result = await db.execute(
            select(Subscription.service_id).where(
                Subscription.firm_id == firm_id
            )
        )
        existing_service_ids = set(existing_result.scalars().all())

        now = utc_now()
        created: list[Subscription] = []

        for service in services:
            if service.id is None:
                continue

            if service.id in existing_service_ids:
                continue

            sub = Subscription(
                firm_id=firm_id,
                service_id=service.id,
                tier_id=None,
                plan_id=None,
                is_on_trial=True,
                trial_started_at=now,
                trial_ends_at=now + timedelta(days=service.trial_days),
                is_active=True,
                subscription_started_at=None,
                subscription_ends_at=None,
                auto_renew=False,
            )

            db.add(sub)
            created.append(sub)

        # No commit here: transaction ownership belongs to the caller.
        return created

    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to create trial subscriptions for firm %s",
            firm_id,
        )
        raise _database_error(
            "creating trial subscriptions",
            exc,
        ) from exc





vs


async def activate_subscription(
    data: SubscriptionActivate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Activate or extend a paid subscription for a firm.

    Admin-only.

    Guards applied:
        - user.state: enabled + verified
        - firm.state: not disabled
    """

    from api.models.users import Firm
    from api.core.guards import guard_user_state, guard_firm_state

    # ------------------------------------------------------------------
    # 1. User state — must be enabled + verified
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Load firm and check state
    # ------------------------------------------------------------------
    try:
        firm = await db.get(Firm, data.firm_id)
    except Exception:
        logger.exception("Failed to load firm id=%s", data.firm_id)
        raise HTTPException(500, "Failed to load firm. Please try again.")

    if not firm:
        raise HTTPException(404, f"Firm with ID {data.firm_id} not found")

    guard_firm_state(firm)

    # ------------------------------------------------------------------
    # 3. Service / tier / plan chain validation
    # ------------------------------------------------------------------
    try:
        service = await db.get(SubscriptionService, data.service_id)
    except Exception:
        logger.exception(
            "Failed to load service id=%s", data.service_id
        )
        raise HTTPException(500, "Failed to load service. Please try again.")

    if not service or not service.is_active:
        raise HTTPException(
            404,
            f"Service with ID {data.service_id} not found or inactive",
        )

    tier = await get_tier_by_id(db, data.tier_id)
    if not tier or not tier.is_active:
        raise HTTPException(
            404, f"Tier with ID {data.tier_id} not found or inactive"
        )

    if tier.service_id != data.service_id:
        raise HTTPException(
            400,
            f"Tier '{tier.name}' does not belong to service "
            f"'{service.name}'",
        )

    plan = await get_plan_by_id(db, data.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(
            404, f"Plan with ID {data.plan_id} not found or inactive"
        )

    if plan.tier_id != data.tier_id:
        raise HTTPException(
            400,
            f"Plan '{plan.name}' does not belong to tier '{tier.name}'",
        )

    # ------------------------------------------------------------------
    # 4. Apply the subscription change
    # ------------------------------------------------------------------
    sub = await get_subscription_by_firm_service(
        db, data.firm_id, data.service_id
    )

    now = datetime.now(timezone.utc)
    ends_at = now + timedelta(days=plan.duration_days)

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

    try:
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to activate subscription firm_id=%s service_id=%s",
            data.firm_id,
            data.service_id,
        )
        raise HTTPException(
            500, "Failed to activate subscription. Please try again."
        )

    logger.info(
        "Subscription activated for firm_id=%s service_id=%s "
        "tier_id=%s plan_id=%s by admin user_id=%s",
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





async def update_subscription(
    firm_id: int,
    service_id: int,
    data: SubscriptionUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Owner of the firm, or admin. Only `auto_renew` is editable.

    Guards:
        - user.state: enabled + verified
        - firm.state: not disabled
    """

    from api.models.users import Firm
    from api.core.guards import guard_user_state, guard_firm_state

    # ------------------------------------------------------------------
    # 1. User state
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Load firm + ownership check
    # ------------------------------------------------------------------
    try:
        firm = await db.get(Firm, firm_id)
    except Exception:
        logger.exception("Failed to load firm id=%s", firm_id)
        raise HTTPException(500, "Failed to load firm. Please try again.")

    if not firm:
        raise HTTPException(404, f"Firm with ID {firm_id} not found")

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            403,
            "You can only update subscriptions for your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    # ------------------------------------------------------------------
    # 3. Firm state
    # ------------------------------------------------------------------
    guard_firm_state(firm)

    # ------------------------------------------------------------------
    # 4. Load subscription
    # ------------------------------------------------------------------
    sub = await get_subscription_by_firm_service(db, firm_id, service_id)
    if not sub:
        raise HTTPException(
            404,
            f"No subscription found for firm {firm_id} on service "
            f"{service_id}",
        )

    if data.auto_renew is None:
        raise HTTPException(
            400, "At least one field must be provided for update"
        )

    if data.auto_renew == sub.auto_renew:
        raise HTTPException(
            400,
            "No changes detected - auto_renew is already set to "
            f"{sub.auto_renew}",
        )

    sub.auto_renew = data.auto_renew

    try:
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to update subscription firm_id=%s service_id=%s",
            firm_id,
            service_id,
        )
        raise HTTPException(
            500, "Failed to update subscription. Please try again."
        )

    logger.info(
        "Subscription auto_renew=%s for firm_id=%s service_id=%s "
        "by user_id=%s",
        sub.auto_renew,
        firm_id,
        service_id,
        current_user.id,
    )

    return {
        "message": (
            f"Auto-renew {'enabled' if sub.auto_renew else 'disabled'} "
            f"successfully"
        ),
        "subscription": _compute_subscription_read(sub),
        }






async def read_subscriptions_for_firm(
    firm_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> list[SubscriptionRead]:
    """
    Owner of the firm, or admin.

    Guards:
        - user.state: enabled + verified
        - NO firm.state guard — owner must always see their own
          subscriptions, including when the firm is disabled
          (so they can reconcile and contact admin).
    """

    from api.models.users import Firm
    from api.core.guards import guard_user_state

    # ------------------------------------------------------------------
    # 1. User state
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Load firm + ownership
    # ------------------------------------------------------------------
    try:
        firm = await db.get(Firm, firm_id)
    except Exception:
        logger.exception("Failed to load firm id=%s", firm_id)
        raise HTTPException(500, "Failed to load firm. Please try again.")

    if not firm:
        raise HTTPException(404, f"Firm with ID {firm_id} not found")

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            403,
            "You can only view subscriptions for your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    # No firm.state guard here — reads remain available to the owner
    # even when the firm is disabled, so they can diagnose the state.

    try:
        result = await db.execute(
            select(Subscription)
            .where(col(Subscription.firm_id) == firm_id)
            .order_by(col(Subscription.service_id))
        )
        subs = result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to list subscriptions for firm_id=%s", firm_id
        )
        raise HTTPException(
            500, "Failed to load subscriptions. Please try again."
        )

    return [_compute_subscription_read(s) for s in subs]



async def read_subscription_status(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> SubscriptionStatusRead:
    """
    Owner of the firm, or admin.

    Guards:
        - user.state: enabled + verified
        - NO firm.state guard — read-only status check must work
          even when the firm is disabled.
    """

    from api.models.users import Firm
    from api.core.guards import guard_user_state

    # ------------------------------------------------------------------
    # 1. User state
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Load firm + ownership
    # ------------------------------------------------------------------
    try:
        firm = await db.get(Firm, firm_id)
    except Exception:
        logger.exception("Failed to load firm id=%s", firm_id)
        raise HTTPException(500, "Failed to load firm. Please try again.")

    if not firm:
        raise HTTPException(404, f"Firm with ID {firm_id} not found")

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            403,
            "You can only view subscriptions for your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    # No firm.state guard — read-only status must remain accessible

    service = await db.get(SubscriptionService, service_id)
    if not service:
        raise HTTPException(
            404, f"Service with ID {service_id} not found"
        )

    sub = await get_subscription_by_firm_service(db, firm_id, service_id)
    if not sub:
        raise HTTPException(
            404,
            f"No subscription found for firm {firm_id} "
            f"on service '{service.name}'",
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








