



async def read_tier_by_slug(
    service_slug: str,
    tier_slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> TierWithPlansRead:
    """
    Fetch a tier (and its plans) by service slug + tier slug.

    Public: 404 if the service is inactive/unbuyable, the tier is
    inactive, or the tier has no active plans.

    Admin: sees everything.
    """
    is_admin = _is_admin(current_user)

    # 1. Service must exist
    service = await get_service_by_slug(db, service_slug)

    if not is_admin:
        if not service.is_active:
            raise HTTPException(
                404, "Subscription tier not found."
            )
        buyable = await _service_has_buyable(db, _service_id(service))
        if not buyable:
            raise HTTPException(
                404, "Subscription tier not found."
            )

    # 2. Tier must exist AND belong to this service
    tier = await get_tier_by_slug(db, tier_slug)

    if tier.service_id != _service_id(service):
        raise HTTPException(
            404, "Subscription tier not found."
        )

    if not is_admin and not tier.is_active:
        raise HTTPException(
            404, "Subscription tier not found."
        )

    # 3. Load plans
    plans_query = (
        select(SubscriptionPlan)
        .where(col(SubscriptionPlan.tier_id) == _tier_id(tier))
        .order_by(col(SubscriptionPlan.duration_days))
    )
    if not is_admin:
        plans_query = plans_query.where(
            col(SubscriptionPlan.is_active).is_(True)
        )

    try:
        result = await db.execute(plans_query)
        plans = result.scalars().all()
    except SQLAlchemyError as exc:
        raise _database_error("reading tier plans", exc) from exc

    if not is_admin and not plans:
        raise HTTPException(
            404, "Subscription tier not found."
        )

    return TierWithPlansRead.model_validate(tier).model_copy(
        update={"plans": [to_plan_read(p) for p in plans]}
    )






# =============================================================================
# CHECKOUT (Step 2)
# =============================================================================

# api/subscriptions/logics.py (additions)

from decimal import Decimal

from api.core.guards import guard_firm_state, guard_user_state
from api.payments.gateways import initiate_payment, verify_payment
from api.models.subscriptions import SubscriptionPayment


# =============================================================================
# CHECKOUT — Step 2
# =============================================================================

async def get_checkout_details(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Load everything needed to render the checkout page.

    Validates:
        - user is enabled + verified
        - user owns at least one active firm
        - plan and its full tier → service chain exist and are active
    """

    # ------------------------------------------------------------------
    # 1. User state
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Pick the user's active firm
    #
    # If they own multiple, prefer the first non-disabled one. If they
    # own none (or all disabled), they need to create a firm first.
    # ------------------------------------------------------------------
    from api.models.users import Firm

    try:
        firms_result = await db.execute(
            select(Firm)
            .where(col(Firm.user_id) == current_user.id)
            .order_by(col(Firm.created_at))
        )
        firms = firms_result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s", current_user.id
        )
        raise HTTPException(500, "Failed to load firm. Please try again.")

    active_firm = next((f for f in firms if not f.disabled), None)

    if active_firm is None:
        if not firms:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You need to create a firm before subscribing.",
                headers={"X-Error-Code": "no_firm"},
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="All your firms are disabled. Please contact admin.",
            headers={"X-Error-Code": "firm_suspended"},
        )

    # ------------------------------------------------------------------
    # 3. Load plan + tier + service
    # ------------------------------------------------------------------
    plan = await get_plan_by_id(db, plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(404, f"Plan id={plan_id} not found or inactive")

    tier = await get_tier_by_id(db, plan.tier_id)
    if not tier or not tier.is_active:
        raise HTTPException(
            404, f"Tier id={plan.tier_id} not found or inactive"
        )

    service = await db.get(SubscriptionService, tier.service_id)
    if not service or not service.is_active:
        raise HTTPException(
            404, f"Service id={tier.service_id} not found or inactive"
        )

    # ------------------------------------------------------------------
    # 4. Payment methods available
    # ------------------------------------------------------------------
    from api.models.home import PaymentMethods

    try:
        pm_result = await db.execute(
            select(PaymentMethods).order_by(col(PaymentMethods.name))
        )
        payment_methods = pm_result.scalars().all()
    except Exception:
        logger.exception("Failed to load payment methods")
        raise HTTPException(
            500, "Failed to load payment methods. Please try again."
        )

    from api.payments.logics import to_payment_method_read
    from api.firms.schemas import FirmRead
    from api.subscriptions.schemas import (
        CheckoutResponse,
        PlanRead,
        ServiceRead,
        TierRead,
    )

    return CheckoutResponse(
        plan=to_plan_read(plan),
        tier=TierRead.model_validate(tier),
        service=ServiceRead.model_validate(service),
        firm=FirmRead.model_validate(active_firm),
        payment_methods=[
            to_payment_method_read(pm) for pm in payment_methods
        ],
        amount=plan.price,
        currency=settings.currency_code,
    )


# =============================================================================
# PURCHASE — Step 4
# =============================================================================

async def initiate_subscription_purchase(
    data: SubscriptionPurchase,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Start a payment. Does NOT activate the subscription yet.

    Validates:
        - user is enabled + verified
        - user has an active firm
        - plan → tier → service chain is valid
        - payment method exists
        - no pending payment for the same firm+plan already in flight

    Creates a SubscriptionPayment record with status="pending",
    calls the gateway, and returns the reference.
    """

    # ------------------------------------------------------------------
    # 1. User state
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Active firm
    # ------------------------------------------------------------------
    from api.models.users import Firm

    try:
        firms_result = await db.execute(
            select(Firm)
            .where(col(Firm.user_id) == current_user.id)
            .order_by(col(Firm.created_at))
        )
        firms = firms_result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s", current_user.id
        )
        raise HTTPException(500, "Failed to load firm. Please try again.")

    active_firm = next((f for f in firms if not f.disabled), None)

    if active_firm is None:
        if not firms:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You need to create a firm before subscribing.",
                headers={"X-Error-Code": "no_firm"},
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="All your firms are disabled. Please contact admin.",
            headers={"X-Error-Code": "firm_suspended"},
        )

    # Firm is guaranteed enabled (we filtered). Guard anyway:
    guard_firm_state(active_firm)

    # ------------------------------------------------------------------
    # 3. Plan → tier → service chain
    # ------------------------------------------------------------------
    plan = await get_plan_by_id(db, data.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(404, f"Plan id={data.plan_id} not found or inactive")

    tier = await get_tier_by_id(db, plan.tier_id)
    if not tier or not tier.is_active:
        raise HTTPException(
            404, f"Tier id={plan.tier_id} not found or inactive"
        )

    service = await db.get(SubscriptionService, tier.service_id)
    if not service or not service.is_active:
        raise HTTPException(
            404, f"Service id={tier.service_id} not found or inactive"
        )

    # ------------------------------------------------------------------
    # 4. Payment method
    # ------------------------------------------------------------------
    from api.models.home import PaymentMethods

    payment_method = await db.get(PaymentMethods, data.payment_method_id)
    if not payment_method:
        raise HTTPException(
            404, f"Payment method id={data.payment_method_id} not found"
        )

    # ------------------------------------------------------------------
    # 5. Reject duplicate pending payments
    #
    # If a user double-clicks Buy Now, don't create two pending payments.
    # ------------------------------------------------------------------
    try:
        existing_pending = (
            await db.execute(
                select(SubscriptionPayment).where(
                    col(SubscriptionPayment.firm_id) == active_firm.id,
                    col(SubscriptionPayment.plan_id) == data.plan_id,
                    col(SubscriptionPayment.status) == "pending",
                )
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check pending payments firm_id=%s", active_firm.id
        )
        raise HTTPException(
            500, "Failed to start purchase. Please try again."
        )

    if existing_pending:
        raise HTTPException(
            409,
            "A payment for this plan is already pending. "
            "Please complete or wait for it to expire.",
            headers={"X-Error-Code": "payment_pending"},
        )

    # ------------------------------------------------------------------
    # 6. Create the pending payment record
    # ------------------------------------------------------------------
    reference = f"sub_{secrets.token_urlsafe(16)}"

    payment = SubscriptionPayment(
        firm_id=active_firm.id,
        plan_id=data.plan_id,
        payment_method_id=data.payment_method_id,
        amount=plan.price,
        currency=settings.currency_code,
        payment_reference=reference,
        status="pending",
    )

    try:
        db.add(payment)
        await db.commit()
        await db.refresh(payment)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create payment record firm_id=%s plan_id=%s",
            active_firm.id,
            data.plan_id,
        )
        raise HTTPException(
            500, "Failed to start purchase. Please try again."
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
            customer_phone=current_user.phone if hasattr(current_user, "phone") else None,
        )
    except Exception:
        logger.exception(
            "Gateway initiation failed for payment ref=%s", reference
        )

        # Mark the payment as failed so it doesn't block new attempts
        payment.status = "failed"
        db.add(payment)
        try:
            await db.commit()
        except Exception:
            await db.rollback()

        raise HTTPException(
            500,
            "Payment gateway is unavailable. Please try again.",
        )

    logger.info(
        "Purchase initiated: firm_id=%s plan_id=%s ref=%s provider=%s",
        active_firm.id,
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
# CONFIRM — Step 5
# =============================================================================







# =============================================================================
# CHECKOUT — Step 2
# =============================================================================

@router.get(
    "/checkout/{plan_id}",
    response_model=CheckoutResponse,
    status_code=status.HTTP_200_OK,
    summary="All data needed for the checkout page",
)
async def checkout_details(
    plan_id: int,
    db: DBDep,
    current_user: CurrentUser,
):
    return await get_checkout_details(
        plan_id=plan_id, db=db, current_user=current_user
    )







# api/subscriptions/logics.py
# (only the read section changes — everything else identical)
# Inside confirm_subscription_payment, after creating/updating `sub`:

# ... existing sub creation / update logic ...

# Attach the payment that activated / extended this subscription
sub.last_payment_id = payment.id

db.add(sub)
db.add(payment)
















