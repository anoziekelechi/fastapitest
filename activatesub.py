

#a[i/subscriptions/helpers.py

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
        raise database_error(
            "looking up a firm's subscription",
            exc,
        ) from exc
        

async def count_subscriptions(
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
        raise database_error("counting subscriptions", exc) from exc
    
def days_until(
    expires_at: datetime | None,
    *,
    now: datetime | None = None,
) -> int | None:
    """Return remaining calendar-duration days, rounded up."""
    expiry = as_utc(expires_at)#Argument of type "datetime | None" cannot be assigned to 
    #parameter "value" of type "datetime" in function "as_utc"

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
    expiry = as_utc(expires_at)#Argument of type "datetime | None" cannot be assigned to
    #parameter "value" of type "datetime" in function "as_utc"

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



#api/subscriptions/logics.py

async def read_subscriptions_for_firm(
    firm_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> list[SubscriptionRead]:
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
            .where(col(Subscription.firm_id) == firm_id)
            .order_by(col(Subscription.service_id))
        )
        subscriptions = result.scalars().all()

    except SQLAlchemyError as exc:
        raise database_error(
            "reading subscriptions for a firm",
            exc,
        ) from exc

    return [
        compute_subscription_read(sub)
        for sub in subscriptions
    ]



async def read_subscription_status(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> SubscriptionStatusRead:
    guard_user_state(current_user)

    await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=False,
    )

    try:
        service = await db.get(
            SubscriptionService,
            service_id,
        )
    except SQLAlchemyError as exc:
        raise database_error(
            "loading the subscription service",
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found for this firm and service.",
        )

    tier = (
        await get_tier_by_id(db, sub.tier_id)
        if sub.tier_id is not None
        else None
    )

    plan = (
        await get_plan_by_id(db, sub.plan_id)
        if sub.plan_id is not None
        else None
    )

    read = compute_subscription_read(sub)

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
    service_id: int,# use slug
    data: SubscriptionUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    guard_user_state(current_user)

    firm = await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=True,
    )

    sub = await get_subscription_by_firm_service(
        db,
        firm.id,#Argument of type "int | None" cannot be assigned to parameter "firm_id" of type "int" in function
        service_id,
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

    await commit_or_rollback(
        db,
        operation="updating a subscription",
    )
    await refresh_or_raise(
        db,
        sub,
        operation="refreshing a subscription",
    )

    logger.info(
        "Subscription auto_renew=%s firm_id=%s service_id=%s "
        "user_id=%s",
        sub.auto_renew,
        firm.id,
        service_id,
        current_user.id,
    )

    return {
        "message": (
            f"Auto-renew {'enabled' if sub.auto_renew else 'disabled'} "
            "successfully."
        ),
        "subscription": compute_subscription_read(sub),
    }


async def create_trial_subscriptions_for_firm(
    db: AsyncSession,
    firm_id: int,
) -> None:
    """
    Create a trial for each active subscription service.

    Called from firm creation; not exposed directly as a route.
    """
    try:
        services_result = await db.execute(
            select(SubscriptionService).where(
                col(SubscriptionService.is_active).is_(True)
            )
        )
        services = services_result.scalars().all()

    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception(
            "Failed to load services for trial creation firm_id=%s",
            firm_id,
        )
        return

    now = utc_now()

    try:
        for service in services:
            service_id = service_id(service)

            existing = await get_subscription_by_firm_service(
                db,
                firm_id,
                service_id,
            )

            if existing is not None:
                continue

            trial_ends_at = now + timedelta(
                days=service.trial_days
            )

            sub = Subscription(
                firm_id=firm_id,
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

        await db.commit()

    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "Failed to create trial subscriptions for firm_id=%s",
            firm_id,
        )


# ACTIVATE SUB BY ADMIN ONLY NO PAYMENT REQUIRED

async def activate_subscription(
    data: SubscriptionActivate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Administrator override: activate or extend a subscription
    without a payment record.
    """
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )

    guard_user_state(current_user)

    try:
        firm = await db.get(Firm, data.firm_id)
        service = await db.get(
            SubscriptionService,
            data.service_id,
        )
    except SQLAlchemyError as exc:
        raise database_error(
            "loading subscription activation resources",
            exc,
        ) from exc

    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firm not found.",
        )

    guard_firm_state(firm)

    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found or inactive.",
        )

    tier = await get_tier_by_id(db, data.tier_id)

    if not tier.is_active or tier.service_id != data.service_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The selected tier does not belong to the specified "
                "service or is inactive."
            ),
        )

    plan = await get_plan_by_id(db, data.plan_id)

    if not plan.is_active or plan.tier_id != tier.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The selected plan does not belong to the specified "
                "tier or is inactive."
            ),
        )

    now = utc_now()

    # Existing helper must use the firm/service unique key.
    sub = await get_subscription_by_firm_service(
        db,
        data.firm_id,
        data.service_id,
    )

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
        # Extend from the existing expiration date.
        base_time = as_utc(sub.subscription_ends_at)#Argument of type "datetime | None" cannot be 
        #assigned to parameter "value" of type "datetime" in function "as_utc"
    else:
        # New subscription, expired subscription, or plan switch.
        base_time = now

    ends_at = add_calendar_months(
        base_time,
        plan.duration_months,
    )

    if sub is None:
        sub = Subscription(
            firm_id=firm.id,#Argument of type "int | None" cannot be assigned to parameter "firm_id" of type "int" in function 
            service_id=service.id,#Argument of type "int | None" cannot be assigned to parameter "service_id" of type "int" in function 
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

        # This activation has no payment.
        # Do not invent or overwrite a payment reference.
        db.add(sub)

    await commit_or_rollback(
        db,
        operation="activating a subscription",
    )
    await refresh_or_raise(
        db,
        sub,
        operation="refreshing a subscription",
    )

    logger.info(
        "Admin activated subscription: firm_id=%s service_id=%s "
        "tier_id=%s plan_id=%s by user_id=%s",
        firm.id,
        service.id,
        tier.id,
        plan.id,
        current_user.id,
    )

    return {
        "message": (
            f"Subscription activated for '{service.name}' until "
            f"{ends_at.strftime('%Y-%m-%d')}."
        ),
        "subscription": compute_subscription_read(sub),
}










