# api/models/subscriptions.py (addition)

class SubscriptionPayment(BaseModel, table=True):
    """
    One payment attempt for activating or extending a subscription.

    A payment goes through these states:
        pending   → user clicked Buy Now, chose a gateway, request sent
        paid      → gateway confirmed, subscription activated
        failed    → gateway rejected
        cancelled → user or gateway cancelled
    """
    __tablename__ = "subscription_payments"  # type: ignore

    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    plan_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_plans.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    payment_method_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("paymentmethods.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    # Snapshot of the amount at purchase time (price can change later)
    amount: Decimal = Field(
        sa_column=Column(Numeric(precision=10, scale=2), nullable=False)
    )
    currency: str = Field(
        sa_column=Column(String(3), nullable=False, server_default="LRD")
    )

    # Gateway reference — what the payment provider returns
    payment_reference: str = Field(
        sa_column=Column(String(100), nullable=False, unique=True, index=True)
    )

    # Status
    status: str = Field(
        default="pending",
        sa_column=Column(
            String(20), nullable=False, server_default="pending"
        )
    )

    # Timestamps
    paid_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True},
    )

    # Relationships
    firm: Optional["Firm"] = Relationship()
    plan: Optional["SubscriptionPlan"] = Relationship()
    payment_method: Optional["PaymentMethods"] = Relationship()





# api/subscriptions/schemas.py (additions)

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# CHECKOUT (Step 2)
# =============================================================================

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

async def confirm_subscription_payment(
    data: SubscriptionPaymentConfirm,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Confirm a payment and activate the subscription.

    In production, this is called by the gateway webhook — but the
    webhook validates the signature. Here we allow the user to
    trigger it (for the stub), and the backend re-verifies with the
    gateway before activating.
    """

    # ------------------------------------------------------------------
    # 1. Load the payment record
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(SubscriptionPayment).where(
                col(SubscriptionPayment.payment_reference)
                == data.payment_reference
            )
        )
        payment = result.scalars().first()
    except Exception:
        logger.exception(
            "Failed to load payment ref=%s", data.payment_reference
        )
        raise HTTPException(500, "Failed to confirm payment.")

    if not payment:
        raise HTTPException(
            404, f"Payment '{data.payment_reference}' not found"
        )

    # ------------------------------------------------------------------
    # 2. Ownership — the firm belongs to the caller (or caller is admin)
    # ------------------------------------------------------------------
    from api.models.users import Firm

    firm = await db.get(Firm, payment.firm_id)
    if not firm:
        raise HTTPException(500, "Firm not found for this payment")

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            403,
            "You can only confirm payments for your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    guard_user_state(current_user)
    guard_firm_state(firm)

    # ------------------------------------------------------------------
    # 3. Idempotency — already paid?
    # ------------------------------------------------------------------
    if payment.status == "paid":
        raise HTTPException(
            409,
            "This payment has already been confirmed.",
        )

    if payment.status != "pending":
        raise HTTPException(
            400,
            f"This payment cannot be confirmed (status: {payment.status}).",
        )

    # ------------------------------------------------------------------
    # 4. Verify with the gateway
    # ------------------------------------------------------------------
    from api.models.home import PaymentMethods

    pm = await db.get(PaymentMethods, payment.payment_method_id)
    if not pm:
        raise HTTPException(500, "Payment method not found")

    provider = pm.name.lower().replace(" ", "_")

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
            500,
            "Payment gateway is unavailable. Please try again.",
        )

    if verification.get("status") != "paid":
        # Gateway says it's not paid yet
        raise HTTPException(
            402,
            "Payment has not been confirmed by the gateway.",
            headers={"X-Error-Code": "payment_pending"},
        )

    # ------------------------------------------------------------------
    # 5. Mark the payment paid
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc)
    payment.status = "paid"
    payment.paid_at = now
    db.add(payment)

    # ------------------------------------------------------------------
    # 6. Activate / extend the subscription
    # ------------------------------------------------------------------
    from api.subscriptions.logics import activate_subscription

    activate_data = SubscriptionActivate(
        firm_id=payment.firm_id,
        service_id=tier_service_id(tier=await get_tier_by_id(db, (await get_plan_by_id(db, payment.plan_id)).tier_id)),
        tier_id=(await get_plan_by_id(db, payment.plan_id)).tier_id,
        plan_id=payment.plan_id,
    )

    # Simpler version below — activate_subscription takes typed data
    plan = await get_plan_by_id(db, payment.plan_id)
    if not plan:
        raise HTTPException(500, "Plan not found for this payment")

    activate_data = SubscriptionActivate(
        firm_id=payment.firm_id,
        service_id=(await get_tier_by_id(db, plan.tier_id)).service_id,
        tier_id=plan.tier_id,
        plan_id=payment.plan_id,
    )

    activation_result = await activate_subscription(
        data=activate_data,
        db=db,
        current_user=current_user,
    )

    logger.info(
        "Payment confirmed ref=%s firm_id=%s plan_id=%s",
        payment.payment_reference,
        payment.firm_id,
        payment.plan_id,
    )

    return {
        "message": "Payment confirmed. Subscription is now active.",
        "payment": SubscriptionPaymentRead.model_validate(payment),
        "subscription": activation_result["subscription"],
    }






# api/subscriptions/routes.py (additions)

from api.subscriptions.logics import (
    confirm_subscription_payment,
    get_checkout_details,
    initiate_subscription_purchase,
)
from api.subscriptions.schemas import (
    CheckoutResponse,
    SubscriptionPaymentConfirm,
    SubscriptionPaymentConfirmedResponse,
    SubscriptionPurchase,
    SubscriptionPurchaseResponse,
)


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


# =============================================================================
# PURCHASE — Step 4
# =============================================================================

@router.post(
    "/purchase",
    response_model=SubscriptionPurchaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initiate a subscription purchase (creates a pending payment)",
)
async def initiate_purchase(
    data: SubscriptionPurchase,
    db: DBDep,
    current_user: CurrentUser,
):
    return await initiate_subscription_purchase(
        data=data, db=db, current_user=current_user
    )


# =============================================================================
# CONFIRM — Step 5
# =============================================================================

@router.post(
    "/purchase/confirm",
    response_model=SubscriptionPaymentConfirmedResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm a payment and activate the subscription",
)
async def confirm_purchase(
    data: SubscriptionPaymentConfirm,
    db: DBDep,
    current_user: CurrentUser,
):
    return await confirm_subscription_payment(
        data=data, db=db, current_user=current_user
    )







# api/subscriptions/logics.py
# (only the read section changes — everything else identical)

from sqlalchemy import exists


# =============================================================================
# STEP 1 — READ SERVICES (buyable services only for public)
# =============================================================================

async def read_services(
    db: AsyncSession,
    *,
    current_user: ReadUser | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[ServiceRead]:
    """
    Return the list of services to display on the pricing landing page.

    Public callers see ONLY services that have:
        - at least one active tier, AND
        - that tier has at least one active plan

    This prevents showing services the user cannot actually buy.

    Admins see all services regardless of tiers/plans.
    """
    query = (
        select(SubscriptionService)
        .order_by(col(SubscriptionService.name))
        .offset(skip)
        .limit(limit)
    )

    if not _is_admin(current_user):
        # Only services that are active AND have at least one active
        # tier with at least one active plan.
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
# STEP 2 — READ SERVICE + ITS TIERS (buyable tiers only for public)
# =============================================================================

async def read_service_by_slug(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceRead:
    """
    Return a single service by slug.

    Public: 404 if the service is inactive OR has no buyable tier/plan.
    Admin:  returns any service.
    """
    service = await get_service_by_slug(db, slug)

    if not _is_admin(current_user):
        # Active service required
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subscription service '{slug}' not found.",
            )

        # At least one buyable tier/plan pair required
        buyable = await _service_has_buyable_tier(db, _service_id(service))
        if not buyable:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subscription service '{slug}' not found.",
            )

    return to_service_read(service)


async def read_tiers_for_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> list[TierRead]:
    """
    List tiers for the given service.

    Public callers see ONLY active tiers that have at least one
    active plan. A tier with no plans is filtered out — because the
    user clicking it would hit a dead end.

    Admins see all tiers.
    """
    service = await get_service_by_slug(db, slug)

    if not _is_admin(current_user):
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subscription service '{slug}' not found.",
            )

    query = (
        select(SubscriptionTier)
        .where(col(SubscriptionTier.service_id) == _service_id(service))
        .order_by(col(SubscriptionTier.name))
    )

    if not _is_admin(current_user):
        # Only active tiers
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
        tiers = result.scalars().all()
    except SQLAlchemyError as exc:
        raise _database_error("reading service tiers", exc) from exc

    return [TierRead.model_validate(t) for t in tiers]


# =============================================================================
# STEP 3 — READ TIER + ITS PLANS
# =============================================================================

async def read_tier_by_id(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> TierRead:
    tier = await get_tier_by_id(db, tier_id)
    if not tier:
        raise HTTPException(404, f"Tier id={tier_id} not found")

    if not _is_admin(current_user):
        if not tier.is_active:
            raise HTTPException(404, f"Tier id={tier_id} not found")

        # Public must not see a tier with no buyable plan
        has_plan = await _tier_has_active_plan(db, tier_id)
        if not has_plan:
            raise HTTPException(404, f"Tier id={tier_id} not found")

    return TierRead.model_validate(tier)


async def read_tier_with_plans(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> TierWithPlansRead:
    """
    Return a single tier with its plans.

    Public: 404 if the tier is inactive OR has no active plan.
    Admin:  returns any tier, with all plans.
    """
    tier = await get_tier_by_id(db, tier_id)
    if not tier:
        raise HTTPException(404, f"Tier id={tier_id} not found")

    is_admin = _is_admin(current_user)

    if not is_admin and not tier.is_active:
        raise HTTPException(404, f"Tier id={tier_id} not found")

    plans_query = (
        select(SubscriptionPlan)
        .where(col(SubscriptionPlan.tier_id) == tier_id)
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
        # Public sees only tiers that have plans
        raise HTTPException(404, f"Tier id={tier_id} not found")

    return TierWithPlansRead.model_validate(tier).model_copy(
        update={"plans": [to_plan_read(p) for p in plans]}
    )


async def read_plans_for_tier(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> list[PlanRead]:
    """Alias of `read_tier_with_plans` returning just the plans array."""
    tier_read = await read_tier_with_plans(
        tier_id=tier_id, db=db, current_user=current_user
    )
    return tier_read.plans


async def read_plan_by_id(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> PlanRead:
    plan = await get_plan_by_id(db, plan_id)
    if not plan:
        raise HTTPException(404, f"Plan id={plan_id} not found")

    if not plan.is_active and not _is_admin(current_user):
        raise HTTPException(404, f"Plan id={plan_id} not found")

    return to_plan_read(plan)


# =============================================================================
# INVARIANT HELPERS
# =============================================================================

async def _service_has_buyable_tier(
    db: AsyncSession,
    service_id: int,
) -> bool:
    """
    True if the service has at least one active tier with at least one
    active plan.
    """
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


async def _tier_has_active_plan(
    db: AsyncSession,
    tier_id: int,
) -> bool:
    """True if the tier has at least one active plan."""
    query = (
        select(SubscriptionPlan.id)
        .where(
            col(SubscriptionPlan.tier_id) == tier_id,
            col(SubscriptionPlan.is_active).is_(True),
        )
        .limit(1)
    )

    try:
        result = await db.execute(query)
        return result.scalars().first() is not None
    except SQLAlchemyError as exc:
        raise _database_error(
            "checking tier availability", exc
        ) from exc







async def initiate_subscription_purchase(
    data: SubscriptionPurchase,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Start a subscription payment for the authenticated user's firm.

    USER-FACING — no admin required.

    Validates:
        - user is enabled + verified
        - target firm exists and belongs to the caller
        - firm is not disabled
        - plan → tier → service chain is active
        - payment method exists
        - no duplicate pending payment for same firm + plan

    Creates a SubscriptionPayment(status="pending"),
    calls the gateway, and returns the reference.

    Does NOT activate the subscription. Activation happens in
    confirm_subscription_payment() after the gateway confirms.
    """

    # ------------------------------------------------------------------
    # 1. User state
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Target firm — must exist, be owned, be enabled
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

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only subscribe for your own firms.",
            headers={"X-Error-Code": "wrong_permission"},
        )

    guard_firm_state(firm)

    # ------------------------------------------------------------------
    # 3. Plan → tier → service chain
    # ------------------------------------------------------------------
    plan = await get_plan_by_id(db, data.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan with ID {data.plan_id} not found or inactive.",
        )

    tier = await get_tier_by_id(db, plan.tier_id)
    if not tier or not tier.is_active:
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
    # 4. Payment method
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
    # 5. Reject duplicate pending payments
    #
    # If the user double-clicks Buy Now, do not create two pending
    # payments for the same firm + plan.
    # ------------------------------------------------------------------
    try:
        existing_pending = (
            await db.execute(
                select(SubscriptionPayment).where(
                    col(SubscriptionPayment.firm_id) == data.firm_id,
                    col(SubscriptionPayment.plan_id) == data.plan_id,
                    col(SubscriptionPayment.status) == "pending",
                )
            )
        ).scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error(
            "checking pending payments", exc
        ) from exc

    if existing_pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A payment for this plan is already pending. "
                "Please complete it or wait for it to expire."
            ),
            headers={"X-Error-Code": "payment_pending"},
        )

    # ------------------------------------------------------------------
    # 6. Create the pending payment record
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

        # Mark the payment as failed so it doesn't block new attempts
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








async def confirm_subscription_payment(
    data: SubscriptionPaymentConfirm,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Confirm a payment and activate the subscription.

    USER-FACING — no admin required.

    In production this runs from the gateway webhook (after signature
    verification). For the current stub, the frontend calls it after
    the user returns from the gateway.

    Flow:
        1. Load the SubscriptionPayment record.
        2. Verify ownership of the firm.
        3. Guard user + firm state.
        4. Reject if already paid (idempotency) or not pending.
        5. Verify payment with the gateway.
        6. Mark payment as paid.
        7. Activate / extend the subscription INLINE.
           (Does not call activate_subscription, which is admin-only.)
    """

    # ------------------------------------------------------------------
    # 1. Load the payment record
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(SubscriptionPayment).where(
                col(SubscriptionPayment.payment_reference)
                == data.payment_reference
            )
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
    # 2. Load the firm + ownership
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

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only confirm payments for your own firms.",
            headers={"X-Error-Code": "wrong_permission"},
        )

    # ------------------------------------------------------------------
    # 3. User + firm state
    # ------------------------------------------------------------------
    guard_user_state(current_user)
    guard_firm_state(firm)

    # ------------------------------------------------------------------
    # 4. Idempotency + state check
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
    # 5. Verify with the gateway
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
    # 6. Load plan → tier → service chain
    # ------------------------------------------------------------------
    plan = await get_plan_by_id(db, payment.plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Plan not found for this payment.",
        )

    tier = await get_tier_by_id(db, plan.tier_id)
    if not tier:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tier not found for this payment.",
        )

    service_id = tier.service_id

    # ------------------------------------------------------------------
    # 7. Mark the payment as paid
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc)
    payment.status = "paid"
    payment.paid_at = now
    db.add(payment)

    # ------------------------------------------------------------------
    # 8. Activate / extend the subscription INLINE
    #
    # This mirrors the logic in activate_subscription() but is
    # user-authorised (the ownership check above already proved
    # the caller owns the firm).
    # ------------------------------------------------------------------
    sub = await get_subscription_by_firm_service(
        db, payment.firm_id, service_id
    )

    ends_at = now + timedelta(days=plan.duration_days)

    if sub is None:
        # First paid subscription for this firm + service
        sub = Subscription(
            firm_id=payment.firm_id,
            service_id=service_id,
            tier_id=plan.tier_id,
            plan_id=plan.id,
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
            and sub.plan_id == plan.id
            and not sub.is_on_trial
            and sub.is_active
            and sub.subscription_ends_at
            and sub.subscription_ends_at > now
        )

        if same_plan:
            # Extend from current end date (no days lost)
            ends_at = sub.subscription_ends_at + timedelta(
                days=plan.duration_days
            )
            sub.subscription_ends_at = ends_at
        else:
            # Upgrade / change plan / restart from today
            sub.tier_id = plan.tier_id
            sub.plan_id = plan.id
            sub.is_on_trial = False
            sub.trial_ends_at = None
            sub.is_active = True
            sub.subscription_started_at = now
            sub.subscription_ends_at = ends_at

        db.add(sub)

    # ------------------------------------------------------------------
    # 9. Commit the payment + subscription together
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
        "plan_id=%s service_id=%s ends_at=%s",
        payment.payment_reference,
        payment.firm_id,
        payment.plan_id,
        service_id,
        ends_at,
    )

    return {
        "message": "Payment confirmed. Subscription is now active.",
        "payment": SubscriptionPaymentRead.model_validate(payment),
        "subscription": _compute_subscription_read(sub),
    }





@router.post(
    "/firms/activate",
    response_model=ActivateSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Activate or extend a firm's paid subscription (admin only)",
)
async def activate(
    data: SubscriptionActivate,
    db: DBDep,
    admin: AdminUser,   # ← ADMIN ONLY
):
    return await activate_subscription(
        data=data, db=db, current_user=admin
    )







# api/subscriptions/routes.py (additions)

from api.subscriptions.logics import (
    confirm_subscription_payment,
    initiate_subscription_purchase,
)
from api.subscriptions.schemas import (
    SubscriptionPaymentConfirm,
    SubscriptionPaymentConfirmedResponse,
    SubscriptionPurchase,
    SubscriptionPurchaseResponse,
)
from api.users.deps import CurrentUser


@router.post(
    "/purchase",
    response_model=SubscriptionPurchaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initiate a subscription purchase (creates a pending payment)",
)
async def initiate_purchase(
    data: SubscriptionPurchase,
    db: DBDep,
    current_user: CurrentUser,          # ← any authenticated user
):
    return await initiate_subscription_purchase(
        data=data, db=db, current_user=current_user
    )


@router.post(
    "/purchase/confirm",
    response_model=SubscriptionPaymentConfirmedResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm a payment and activate the subscription",
)
async def confirm_purchase(
    data: SubscriptionPaymentConfirm,
    db: DBDep,
    current_user: CurrentUser,          # ← any authenticated user
):
    return await confirm_subscription_payment(
        data=data, db=db, current_user=current_user
    )






# api/models/subscriptions.py

class Subscription(BaseModel, table=True):
    __tablename__ = "subscriptions"  # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "firm_id",
            "service_id",
            name="unique_subscription_per_firm_service",
        ),
    )

    # ... existing columns ...

    firm_id: int = Field(...)
    service_id: int = Field(...)
    tier_id: int | None = Field(...)
    plan_id: int | None = Field(...)

    # --- NEW ---
    last_payment_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "subscription_payments.id",
                ondelete="SET NULL",   # if the payment row is deleted,
                                       # the subscription survives
            ),
            nullable=True,
            index=True,
        ),
    )

    # ... existing columns (trial_*, subscription_*, auto_renew) ...

    # Relationships
    firm: Optional["Firm"] = Relationship(back_populates="subscriptions")
    service: Optional["SubscriptionService"] = Relationship(
        back_populates="subscriptions"
    )
    tier: Optional["SubscriptionTier"] = Relationship(
        back_populates="subscriptions"
    )
    plan: Optional["SubscriptionPlan"] = Relationship(
        back_populates="subscriptions"
    )

    # --- NEW ---
    last_payment: Optional["SubscriptionPayment"] = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Subscription.last_payment_id]",
        },
    )


# api/subscriptions/schemas.py

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






# Inside confirm_subscription_payment, after creating/updating `sub`:

# ... existing sub creation / update logic ...

# Attach the payment that activated / extended this subscription
sub.last_payment_id = payment.id

db.add(sub)
db.add(payment)
















