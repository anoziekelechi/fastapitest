

# =============================================================================
# FIRM STATE — pure predicate on a loaded firm
# =============================================================================

def guard_firm_state(firm: Firm) -> None:
    """
    Reject operations on a disabled firm.

    Pure state check — does not load the firm, does not check ownership.
    Use when you already have the firm and only need the state check
    (e.g. after loading it inside a loop, or from a background task).
    """
    if firm.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Firm '{firm.name}' is suspended. Please contact admin.",
            headers={"X-Error-Code": "firm_suspended"},
        )


# =============================================================================
# FIRM ACCESS — load + ownership + optional state
# =============================================================================

async def require_firm_access(
    db: AsyncSession,
    *,
    firm_id: int,
    current_user: ReadUser,
    require_active: bool = True,
) -> Firm:
    """
    Load a firm and verify the caller can act on it.

    Steps:
        1. Load the firm (404 if missing).
        2. Reject if the caller is neither the owner nor an admin.
        3. When `require_active=True` (default), reject disabled firms.

    Does NOT check user state — callers that need it should call
    `guard_user_state(current_user)` first.

    Default is `require_active=True` because 99% of call sites are
    money-touching operations where acting on a disabled firm is wrong.
    The 1% that need to *read* a disabled firm should pass
    `require_active=False` explicitly, so the choice is deliberate.
    """
    from api.core.logging import logger  # or wherever your logger lives

    try:
        firm = await db.get(Firm, firm_id)
    except SQLAlchemyError as exc:
        logger.exception("Failed to load firm id=%s", firm_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        ) from exc

    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firm not found.",
        )

    if not is_admin(current_user) and firm.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this firm.",
            headers={"X-Error-Code": "wrong_permission"},
        )

    if require_active:
        guard_firm_state(firm)

    return firm





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

    Guards:
        - guard_user_state: enabled + verified user
        - require_firm_access: firm exists, caller owns it (or admin),
          firm is enabled

    Idempotency:
        - Redis caches (user_id, idempotency_key) → payment_reference
          for 24h. A repeated call returns the original payment info.
        - A pending payment for the same (firm, plan) blocks a new
          initiate. Stale pendings are auto-expired by
          `get_pending_payment`.

    Creates a SubscriptionPayment(status="pending"),
    calls the gateway, returns the reference.
    """
    from api.core.guards import guard_user_state, require_firm_access

    # ------------------------------------------------------------------
    # 1. User state
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Idempotency — same key → return the cached response
    # ------------------------------------------------------------------
    idem_key = f"purchase_idem:{current_user.id}:{data.idempotency_key}"

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
    # 3. Firm — load, check ownership, reject disabled (all-in-one)
    # ------------------------------------------------------------------
    firm = await require_firm_access(
        db,
        firm_id=data.firm_id,
        current_user=current_user,
        require_active=True,
    )

    # ------------------------------------------------------------------
    # 4. Plan → tier → service chain
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
    # 5. Payment method
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
    # 6. Existing pending payment (with staleness check)
    # ------------------------------------------------------------------
    existing_pending = await get_pending_payment(
        db, data.firm_id, data.plan_id
    )

    if existing_pending:
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
    # 7. Create the pending payment
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
    # 8. Cache the idempotency key
    # ------------------------------------------------------------------
    try:
        await redis.set(idem_key, reference, ex=IDEMPOTENCY_TTL_SECONDS)
    except Exception:
        logger.exception(
            "Redis write failed for idempotency key=%s", idem_key
        )

    # ------------------------------------------------------------------
    # 9. Call the gateway
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



#schemas used



# =============================================================================
# SUBSCRIPTION
# =============================================================================

class SubscriptionActivate(BaseModel):
    """Activate (or extend) a paid subscription for a firm."""
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(..., gt=0)
    service_id: int = Field(..., gt=0)
    tier_id: int = Field(..., gt=0)
    plan_id: int = Field(..., gt=0)


class SubscriptionUpdate(BaseModel):
    # This update requires auto_renew to be provided and non-null.
    auto_renew: bool

    @model_validator(mode="before")
    @classmethod
    def require_auto_renew(cls, value: object) -> object:
        if not isinstance(value, dict):
            raise ValueError("A valid update object is required.")

        if "auto_renew" not in value:
            raise ValueError("auto_renew must be provided.")

        if value["auto_renew"] is None:
            raise ValueError("auto_renew cannot be null.")

        return value


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    service_id: int
    tier_id: int | None = None
    plan_id: int | None = None

    # --- NEW ---
    last_payment_id: int | None = None

    is_on_trial: bool
    trial_started_at: datetime
    trial_ends_at: datetime | None = None

    is_active: bool
    subscription_started_at: datetime | None = None
    subscription_ends_at: datetime | None = None
    auto_renew: bool

    created_at: datetime
    updated_at: datetime

    days_remaining: int | None = None
    status_label: str | None = None
    can_access: bool = False


class SubscriptionStatusRead(BaseModel):
    """Quick status check for one firm + one service."""
    firm_id: int
    service_id: int
    service_name: str
    tier_name: str | None = None
    plan_name: str | None = None
    is_active: bool
    is_on_trial: bool
    days_remaining: int
    status_label: str
    can_access: bool


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreatePlanResponse(BaseModel):
    message: str
    plan: PlanRead


class UpdatePlanResponse(BaseModel):
    message: str
    plan: PlanRead


class ActivateSubscriptionResponse(BaseModel):
    message: str
    subscription: SubscriptionRead


class UpdateSubscriptionResponse(BaseModel):
    message: str
    subscription: SubscriptionRead








class CheckoutResponse(BaseModel):
    """
    All the info the user needs to complete the purchase.

    Returned when the user lands on /subscriptions/checkout/{plan_id}.
    """
    plan: "PlanRead"
    tier: "TierRead"
    service: "ServiceRead"
    firm: "FirmRead"
    payment_methods: list["PaymentMethodRead"]
    amount: Decimal
    currency: str


# =============================================================================
# PURCHASE (Step 4)
# =============================================================================

class SubscriptionPurchase(BaseModel):
    """User picks a plan + a payment method to initiate purchase."""
    model_config = ConfigDict(extra="forbid")

    plan_id: int = Field(..., gt=0)
    payment_method_id: int = Field(..., gt=0)


class SubscriptionPurchaseResponse(BaseModel):
    """
    Returned after initiating a payment.

    In the Momo Liberia integration, `provider_url` may be a redirect
    URL. For a stub, `payment_reference` is the client's reference
    and the frontend can simulate confirmation via the confirm endpoint.
    """
    message: str
    payment_reference: str
    amount: Decimal
    currency: str
    status: str
    provider_url: str | None = None


# =============================================================================
# PAYMENT CONFIRMATION (Step 5)
# =============================================================================

class SubscriptionPaymentConfirm(BaseModel):
    """Confirm a payment (called after gateway redirect or webhook)."""
    model_config = ConfigDict(extra="forbid")

    payment_reference: str = Field(..., min_length=10)


class SubscriptionPaymentRead(BaseModel):
    """Full payment record for admin/audit views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    plan_id: int
    payment_method_id: int
    amount: Decimal
    currency: str
    payment_reference: str
    status: str
    paid_at: datetime | None
    created_at: datetime


class SubscriptionPaymentConfirmedResponse(BaseModel):
    message: str
    payment: SubscriptionPaymentRead
    subscription: "SubscriptionRead"






from api.firm.schemas import FirmRead
from api.order.schemas import PaymentMethodRead
from api.services.schemas import TierRead,ServiceRead

CheckoutResponse.model_rebuild()












# =============================================================================
# SUBSCRIPTION
# =============================================================================

class SubscriptionActivate(BaseModel):
    """Activate (or extend) a paid subscription for a firm."""
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(..., gt=0)
    service_id: int = Field(..., gt=0)
    tier_id: int = Field(..., gt=0)
    plan_id: int = Field(..., gt=0)


# class SubscriptionUpdate(BaseModel):
#     """Only auto_renew is user-editable."""
#     model_config = ConfigDict(extra="forbid")

#     auto_renew: bool | None = None

class SubscriptionUpdate(BaseModel):
    # This update requires auto_renew to be provided and non-null.
    auto_renew: bool

    @model_validator(mode="before")
    @classmethod
    def require_auto_renew(cls, value: object) -> object:
        if not isinstance(value, dict):
            raise ValueError("A valid update object is required.")

        if "auto_renew" not in value:
            raise ValueError("auto_renew must be provided.")

        if value["auto_renew"] is None:
            raise ValueError("auto_renew cannot be null.")

        return value


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    service_id: int
    tier_id: int | None = None
    plan_id: int | None = None

    # --- NEW ---
    last_payment_id: int | None = None

    is_on_trial: bool
    trial_started_at: datetime
    trial_ends_at: datetime | None = None

    is_active: bool
    subscription_started_at: datetime | None = None
    subscription_ends_at: datetime | None = None
    auto_renew: bool

    created_at: datetime
    updated_at: datetime

    days_remaining: int | None = None
    status_label: str | None = None
    can_access: bool = False





# class SubscriptionRead(BaseModel):
#     model_config = ConfigDict(from_attributes=True)

#     id: int
#     firm_id: int
#     service_id: int
#     tier_id: int | None = None
#     plan_id: int | None = None

#     is_on_trial: bool
#     trial_started_at: datetime
#     trial_ends_at: datetime | None = None

#     is_active: bool
#     subscription_started_at: datetime | None = None
#     subscription_ends_at: datetime | None = None
#     auto_renew: bool

#     created_at: datetime
#     updated_at: datetime

#     # Computed by the service layer
#     days_remaining: int | None = None
#     status_label: str | None = None
#     can_access: bool = False


class SubscriptionStatusRead(BaseModel):
    """Quick status check for one firm + one service."""
    firm_id: int
    service_id: int
    service_name: str
    tier_name: str | None = None
    plan_name: str | None = None
    is_active: bool
    is_on_trial: bool
    days_remaining: int
    status_label: str
    can_access: bool




