


async def update_plan(
    slug: str,
    data: PlanUpdate,
    db: AsyncSession,
) -> dict:
    plan = await get_plan_by_slug(db, slug)
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

    await commit_or_rollback(db, operation="updating a plan")
    await refresh_or_raise(db, plan, operation="refreshing a plan")

    return {
        "message": "Subscription plan updated successfully.",
        "plan": to_plan_read(plan),
    }







from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.core.subscription_durations import SubscriptionDuration


class SubscriptionPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    duration_months: SubscriptionDuration
    price: float = Field(gt=0)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Plan name must be a string.")

        normalized = " ".join(value.split())

        if not normalized:
            raise ValueError("Plan name cannot be empty.")

        return normalized






from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.core.subscription_durations import SubscriptionDuration


class SubscriptionPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    duration_months: SubscriptionDuration | None = None
    price: float | None = Field(default=None, gt=0)
    is_active: bool | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        non_nullable_fields = {
            "name",
            "duration_months",
            "price",
            "is_active",
        }

        for field_name in non_nullable_fields:
            if (
                field_name in self.model_fields_set
                and getattr(self, field_name) is None
            ):
                raise ValueError(
                    f"{field_name} cannot be null."
                )

        return self



class SubscriptionPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tier_id: int
    name: str
    description: str | None
    duration_months: SubscriptionDuration
    price: float
    slug: str
    is_active: bool



from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col

from api.core.subscription_durations import (
    ALLOWED_SUBSCRIPTION_DURATIONS,
)


async def create_plan(
    data: SubscriptionPlanCreate,
    db: AsyncSession,
    current_user: ReadUser,
):
    if not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )

    # Defensive check in case the function is called with an object
    # constructed without normal Pydantic validation.
    if data.duration_months not in ALLOWED_SUBSCRIPTION_DURATIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Invalid subscription duration. Allowed durations "
                "are 1, 2, 3, 6, and 12 months."
            ),
        )

    try:
        tier = await get_tier_by_id(db, data.tier_id)

        if not tier.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot create a plan for an inactive tier.",
            )

        existing_plan_id = await db.scalar(
            select(SubscriptionPlan.id).where(
                col(SubscriptionPlan.tier_id) == tier.id,
                col(SubscriptionPlan.name) == data.name,
            )
        )

        if existing_plan_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A plan with this name already exists for the tier.",
            )

        plan = SubscriptionPlan(
            tier_id=tier.id,
            name=data.name,
            description=data.description,
            duration_months=data.duration_months,
            price=data.price,
            slug=generate_slug(
                f"{tier.slug or tier.name}-{data.name}"
            ),
            is_active=True,
        )

        db.add(plan)

        await commit_or_rollback(
            db,
            operation="creating a plan",
        )

        await refresh_or_raise(
            db,
            plan,
            operation="refreshing a plan",
        )

        return {
            "message": "Subscription plan created successfully.",
            "plan": to_plan_read(plan),
        }

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        await db.rollback()
        raise database_error("creating a plan", exc) from exc










# api/payments/gateways.py
"""
Payment gateway adapters.

Each gateway exposes:
    initiate_payment(amount, currency, reference, phone) → dict
    verify_payment(reference) → dict

Today only Momo Liberia is implemented as a stub.
"""

import logging
import secrets

logger = logging.getLogger(__name__)


# =============================================================================
# MOMO LIBERIA (STUB)
# =============================================================================

async def initiate_momo_liberia(
    amount: str,
    currency: str,
    reference: str,
    customer_phone: str | None = None,
) -> dict:
    """
    Initiate a Momo Liberia payment.

    STUB — always returns a "pending" status. In production this
    would POST to Momo Liberia's API and return the provider's
    response (typically a redirect URL or USSD push code).
    """
    logger.info(
        "Momo Liberia stub: initiating payment ref=%s amount=%s %s",
        reference,
        amount,
        currency,
    )

    # In real integration:
    #   - POST to Momo Liberia API
    #   - pass { amount, currency, reference, phone }
    #   - receive { status, provider_reference, redirect_url }

    return {
        "status": "pending",
        "provider_reference": reference,
        "provider_url": None,   # would be Momo's hosted payment page
        "raw": {"stub": True},
    }


async def verify_momo_liberia(reference: str) -> dict:
    """
    Verify a Momo Liberia payment.

    STUB — always returns "paid". In production this would query
    Momo Liberia's API for the payment status.
    """
    logger.info("Momo Liberia stub: verifying payment ref=%s", reference)

    return {
        "status": "paid",
        "provider_reference": reference,
        "raw": {"stub": True},
    }


# =============================================================================
# DISPATCH
# =============================================================================

async def initiate_payment(
    provider: str,
    amount: str,
    currency: str,
    reference: str,
    customer_phone: str | None = None,
) -> dict:
    if provider.lower() == "momo_liberia":
        return await initiate_momo_liberia(
            amount=amount,
            currency=currency,
            reference=reference,
            customer_phone=customer_phone,
        )
    raise ValueError(f"Unsupported provider: {provider}")


async def verify_payment(provider: str, reference: str) -> dict:
    if provider.lower() == "momo_liberia":
        return await verify_momo_liberia(reference)
    raise ValueError(f"Unsupported provider: {provider}")



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


# new
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


  

# new updates 
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
