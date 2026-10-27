
class SubscriptionService(BaseModel, table=True):
    """
    Available subscription services stored in DB.
    Admin adds new services here - no code changes needed.

    Examples:
        receipt → Receipt generation service
        debtors → Debtor tracking service
    """
    __tablename__ = "subscription_services" # type: ignore

    name: str = Field(
        sa_column=Column(String(50), nullable=False, unique=True, index=True)
        # e.g. "receipt", "debtors"
    )
    
    service_logo_key: str | None = Field(
    default=None,
    sa_column=Column(String(500),nullable=True),
    )
    description: str = Field(
        sa_column=Column(Text, nullable=False)
    )
    slug: str | None = Field(
        default=None,
        sa_column=Column(String(100), nullable=True, unique=True, index=True)
    )
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true")
    )
    trial_days: int = Field(
        default=30,
        sa_column=Column(Integer, nullable=False, server_default="30")
    )

    # Relationships
    tiers: list["SubscriptionTier"] = Relationship(
        back_populates="service"
    )
    subscriptions: list["Subscription"] = Relationship(
        back_populates="service"
    )
    orders: list["Order"] = Relationship(back_populates="service")


class SubscriptionTier(BaseModel, table=True):
    """
    Subscription tiers per service.
    Defines WHAT features you get.

    Examples:
        receipt service:
            starter → basic receipts, 100/month
            pro     → unlimited receipts, custom branding, email delivery

        debtors service:
            starter → track up to 50 debtors
            pro     → unlimited debtors, reminders, reports
    """
    __tablename__ = "subscription_tiers" # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "name",
            "service_id",
            name="unique_tier_per_service"
        ),
    )

    # FK to SubscriptionService
    service_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_services.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    name: str = Field(
        sa_column=Column(String(50), nullable=False)
        # e.g. "starter", "pro", "enterprise"
    )
    slug: str | None = Field(
        default=None,
        sa_column=Column(String(150), nullable=True, unique=True, index=True)
    )
    description: str = Field(
        sa_column=Column(Text, nullable=False)
    )
   
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true")
    )

    # Relationships
    service: Optional["SubscriptionService"] = Relationship(
        back_populates="tiers"
    )
    plans: list["SubscriptionPlan"] = Relationship(
        back_populates="tier"
    )
    subscriptions: list["Subscription"] = Relationship(
        back_populates="tier"
    )
    
    orders: list["Order"] = Relationship(back_populates="tier")


class SubscriptionPlan(BaseModel, table=True):
    """
    Subscription plans per tier.
    Defines HOW LONG and HOW MUCH.

    Examples:
        starter tier:
            monthly   → 30 days,  USD 5
            quarterly → 90 days,  USD 12
            biannual  → 180 days, USD 20
            annual    → 365 days, USD 35

        pro tier:
            monthly   → 30 days,  USD 10
            quarterly → 90 days,  USD 25
            biannual  → 180 days, USD 45
            annual    → 365 days, USD 70
    """
    __tablename__ = "subscription_plans" # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "name",
            "tier_id",
            name="unique_plan_per_tier"
        ),
        CheckConstraint(
            "duration_months IN (1, 2, 3, 6, 12)",
            name="ck_subscription_plan_duration_months",
        ),
    )

    # FK to SubscriptionTier
    tier_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_tiers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    name: str = Field(
        sa_column=Column(String(50), nullable=False)
        # e.g. "monthly", "quarterly", "biannual", "annual"
    )
    slug: str | None = Field(
        default=None,
        sa_column=Column(String(200), nullable=False, unique=True, index=True)
    )
    description: str = Field(
        sa_column=Column(Text, nullable=False)
    )
    duration_months: int = Field(
        sa_column=Column(Integer, nullable=False),
    )

    price: Decimal = Field(
        sa_column=Column(Numeric(precision=10, scale=2), nullable=False)
    )
   
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true")
    )

    # Relationships
    tier: Optional["SubscriptionTier"] = Relationship(
        back_populates="plans"
    )
    subscriptions: list["Subscription"] = Relationship(
        back_populates="plan"
    )
    orders: list["Order"] =  Relationship(back_populates="plan")
    subscription_payments:list["SubscriptionPayment"]= Relationship(back_populates="plan")



class Subscription(BaseModel, table=True):
    """
    Represents a firm's access to a subscription service.

    A subscription can be either a trial or a paid subscription.

    Trial lifecycle:
        is_on_trial = True
        trial_started_at and trial_ends_at are populated.

    Paid subscription lifecycle:
        is_on_trial = False
        subscription_started_at and subscription_ends_at are populated.

    A firm can have only one subscription record per service.
    """

    __tablename__ = "subscriptions"  # type: ignore

    __table_args__ = (
        UniqueConstraint(
            "firm_id",
            "service_id",
            name="unique_subscription_per_firm_service",
        ),
    )

    # ------------------------------------------------------------------
    # Foreign keys
    # ------------------------------------------------------------------

    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    service_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_services.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    tier_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("subscription_tiers.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    plan_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("subscription_plans.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    # ------------------------------------------------------------------
    # Last successful payment
    # ------------------------------------------------------------------

    last_payment_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "subscription_payments.id",
                ondelete="SET NULL",
            ),
            nullable=True,
            index=True,
        ),
    )

    # ------------------------------------------------------------------
    # Trial fields
    # ------------------------------------------------------------------

    is_on_trial: bool = Field(
        default=False,
        index=True,
    )

    trial_started_at: datetime | None = Field(
        default=None,
        nullable=True,
    )

    trial_ends_at: datetime | None = Field(
        default=None,
        nullable=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Subscription status
    # ------------------------------------------------------------------

    is_active: bool = Field(
        default=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Paid subscription dates
    # ------------------------------------------------------------------

    subscription_started_at: datetime | None = Field(
        default=None,
        nullable=True,
    )

    subscription_ends_at: datetime | None = Field(
        default=None,
        nullable=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Renewal settings
    # ------------------------------------------------------------------

    auto_renew: bool = Field(
        default=False,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    firm: Optional["Firm"] = Relationship(
        back_populates="subscriptions",
    )

    service: Optional["SubscriptionService"] = Relationship(
        back_populates="subscriptions",
    )

    tier: Optional["SubscriptionTier"] = Relationship(
        back_populates="subscriptions",
    )

    plan: Optional["SubscriptionPlan"] = Relationship(
        back_populates="subscriptions",
    )

    last_payment: Optional["SubscriptionPayment"] = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Subscription.last_payment_id]",
        },
    )

  
  





class Order(BaseModel, table=True):
    """
    Represents a customer's subscription purchase.

    The order records the selected service, tier, plan, duration,
    and financial details at the time of purchase.

    Typical lifecycle:
        pending_payment -> paid
        pending_payment -> cancelled
        pending_payment -> expired
    """

    __tablename__ = "orders"  # type: ignore

    __table_args__ = (
        UniqueConstraint(
            "order_number",
            name="uq_orders_order_number",
        ),
        CheckConstraint(
            "duration_months > 0",
            name="ck_orders_duration_months_positive",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_orders_amount_positive",
        ),
        CheckConstraint(
            "discount_amount >= 0",
            name="ck_orders_discount_nonnegative",
        ),
        CheckConstraint(
            "total_amount > 0",
            name="ck_orders_total_positive",
        ),
        CheckConstraint(
            "status IN ('pending_payment', 'paid', 'cancelled', 'expired')",
            name="ck_orders_status",
        ),
    )

    # ------------------------------------------------------------------
    # Order identification
    # ------------------------------------------------------------------

    order_number: str = Field(
        sa_column=Column(
            String(64),
            nullable=False,
            unique=True,
            index=True,
        )
    )

    # ------------------------------------------------------------------
    # Foreign keys
    # ------------------------------------------------------------------

    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    service_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_services.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    tier_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_tiers.id", ondelete="RESTRICT"),
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

    # ------------------------------------------------------------------
    # Purchase snapshots
    # ------------------------------------------------------------------

    service_name: str = Field(
        sa_column=Column(
            String(120),
            nullable=False,
        )
    )

    tier_name: str = Field(
        sa_column=Column(
            String(120),
            nullable=False,
        )
    )

    plan_name: str = Field(
        sa_column=Column(
            String(120),
            nullable=False,
        )
    )

    duration_months: int = Field(
        gt=0,
        sa_column=Column(
            Integer,
            nullable=False,
        ),
    )

    # ------------------------------------------------------------------
    # Financial details
    # ------------------------------------------------------------------

    amount: Decimal = Field(
        sa_column=Column(
            Numeric(precision=10, scale=2),
            nullable=False,
        )
    )

    discount_amount: Decimal = Field(
        default=Decimal("0.00"),
        sa_column=Column(
            Numeric(precision=10, scale=2),
            nullable=False,
            default=Decimal("0.00"),
            server_default="0.00",
        ),
    )

    total_amount: Decimal = Field(
        sa_column=Column(
            Numeric(precision=10, scale=2),
            nullable=False,
        )
    )

    currency: str = Field(
        default="LRD",
        sa_column=Column(
            String(3),
            nullable=False,
            server_default="LRD",
        ),
    )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    status: str = Field(
        default="pending_payment",
        sa_column=Column(
            String(30),
            nullable=False,
            server_default="pending_payment",
            index=True,
        ),
    )

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------

    paid_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True},
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    firm: Optional["Firm"] = Relationship(back_populates="orders")
    service: Optional["SubscriptionService"] = Relationship(back_populates="orders")
    tier: Optional["SubscriptionTier"] = Relationship(back_populates="orders")
    plan: Optional["SubscriptionPlan"] = Relationship(back_populates="orders")
    payments:list["SubscriptionPayment"] = Relationship(back_populates="order")








class SubscriptionPayment(BaseModel, table=True):
    """
    One payment attempt for an order.

    Typical lifecycle:
        pending -> paid
        pending -> failed
        pending -> cancelled

    A payment can remain pending while awaiting gateway confirmation
    or reconciliation after an uncertain gateway response.
    """

    __tablename__ = "subscription_payments"  # type: ignore

    __table_args__ = (
        UniqueConstraint(
            "firm_id",
            "idempotency_key",
            name="uq_subscription_payment_firm_idempotency",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_subscription_payment_amount_positive",
        ),
        CheckConstraint(
            "status IN ('pending', 'paid', 'failed', 'cancelled')",
            name="ck_subscription_payment_status",
        ),
    )

    # ------------------------------------------------------------------
    # Relationships to existing records
    # ------------------------------------------------------------------

    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    # New: payment belongs to an order.
    order_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    # Retained for convenient access and compatibility with existing logic.
    # The plan should match the plan recorded on the associated order.
    plan_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey(
                "subscription_plans.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
            index=True,
        )
    )

    payment_method_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey(
                "paymentmethods.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
            index=True,
        )
    )

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    idempotency_key: str = Field(
        sa_column=Column(
            String(100),
            nullable=False,
        )
    )

    # ------------------------------------------------------------------
    # Payment details
    # ------------------------------------------------------------------

    amount: Decimal = Field(
        sa_column=Column(
            Numeric(precision=10, scale=2),
            nullable=False,
        )
    )

    currency: str = Field(
        default="USD",
        sa_column=Column(
            String(3),
            nullable=False,
            server_default="USD",
        ),
    )

    payment_reference: str = Field(
        sa_column=Column(
            String(100),
            nullable=False,
            unique=True,
            index=True,
        )
    )

    provider_reference: str | None = Field(
        default=None,
        sa_column=Column(
            String(150),
            nullable=True,
            index=True,
        ),
    )

    provider_code: str = Field(
        sa_column=Column(
            String(30),
            nullable=False,
        )
    )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    status: str = Field(
        default="pending",
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="pending",
        ),
    )

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------

    paid_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True},
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    firm: Optional["Firm"] = Relationship(back_populates="subscription_payments")
    order: Optional["Order"] = Relationship(back_populates="payments")
    plan: Optional["SubscriptionPlan"] = Relationship(back_populates="subscription_payments")
    payment_method: Optional["PaymentMethods"] = Relationship(back_populates="subscription_payments")
from pydantic import BaseModel, ConfigDict, Field


class SubscriptionPurchase(BaseModel):
    """Initiate a subscription purchase for a firm."""
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(gt=0)
    plan_id: int = Field(gt=0)
    payment_method_id: int = Field(gt=0)

    # Client-generated UUID or similarly unique request key.
    idempotency_key: str = Field(
        min_length=16,
        max_length=128,
    )


# =============================================================================
# PURCHASE — Initiate payment
# =============================================================================

import secrets

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col


SUPPORTED_PAYMENT_PROVIDERS = frozenset(
    {"momo", "orange"}
)


async def initiate_subscription_purchase(
    data: SubscriptionPurchase,
    db: AsyncSession,
    redis: Redis,
    current_user: ReadUser,
) -> dict:
    """
    Create a pending subscription payment and initiate the gateway request.

    This function never activates a subscription.
    """

    # ------------------------------------------------------------------
    # 1. Validate the user
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Validate firm ownership and state
    # ------------------------------------------------------------------
    firm = await require_firm_access(
        db,
        firm_id=data.firm_id,
        current_user=current_user,
        require_active=True,
    )

    # ------------------------------------------------------------------
    # 3. Idempotency: find an existing request with the same key
    # ------------------------------------------------------------------
    idem_key = (
        f"subscription_purchase:"
        f"{current_user.id}:"
        f"{data.idempotency_key}"
    )

    try:
        cached_reference = await redis.get(idem_key)

    except Exception:
        logger.exception(
            "Redis idempotency lookup failed for user_id=%s",
            current_user.id,
        )
        cached_reference = None

    if cached_reference:
        if isinstance(cached_reference, bytes):
            cached_reference = cached_reference.decode("utf-8")

        try:
            result = await db.execute(
                select(SubscriptionPayment).where(
                    col(
                        SubscriptionPayment.payment_reference
                    ) == cached_reference
                )
            )

            existing_payment = result.scalars().first()

        except SQLAlchemyError as exc:
            raise _database_error(
                "loading an idempotent subscription payment",
                exc,
            ) from exc

        if existing_payment is not None:
            # Do not return a cached payment belonging to another firm.
            if existing_payment.firm_id != firm.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "This idempotency key was already used "
                        "for a different purchase."
                    ),
                )

            return {
                "message": "Payment request already exists.",
                "payment_reference": (
                    existing_payment.payment_reference
                ),
                "amount": existing_payment.amount,
                "currency": existing_payment.currency,
                "status": existing_payment.status,
                "provider_url": None,
            }

    # ------------------------------------------------------------------
    # 4. Validate the selected plan
    # ------------------------------------------------------------------
    plan = await get_plan_by_id(db, data.plan_id)

    if not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The selected plan is unavailable.",
        )

    # ------------------------------------------------------------------
    # 5. Validate tier and service
    # ------------------------------------------------------------------
    tier = await get_tier_by_id(db, plan.tier_id)

    if not tier.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The selected subscription tier is unavailable.",
        )

    try:
        service = await db.get(
            SubscriptionService,
            tier.service_id,
        )

    except SQLAlchemyError as exc:
        raise _database_error(
            "loading the subscription service",
            exc,
        ) from exc

    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The selected subscription service is unavailable.",
        )

    # ------------------------------------------------------------------
    # 6. Validate payment method and provider
    # ------------------------------------------------------------------
    try:
        payment_method = await db.get(
            PaymentMethods,
            data.payment_method_id,
        )

    except SQLAlchemyError as exc:
        raise _database_error(
            "loading the payment method",
            exc,
        ) from exc

    if payment_method is None or not payment_method.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The selected payment method is unavailable.",
        )

    provider = payment_method.provider_code

    if provider not in SUPPORTED_PAYMENT_PROVIDERS:
        logger.error(
            "Unsupported provider configured for payment_method_id=%s",
            payment_method.id,
        )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The selected payment method is not supported.",
        )

    # ------------------------------------------------------------------
    # 7. Prevent duplicate pending purchases
    # ------------------------------------------------------------------
    existing_pending = await get_pending_payment(
        db,
        firm.id,
        plan.id,
    )

    if existing_pending is not None:
        # Cache the existing reference so retries can find it.
        try:
            await redis.set(
                idem_key,
                existing_pending.payment_reference,
                ex=IDEMPOTENCY_TTL_SECONDS,
            )

        except Exception:
            logger.exception(
                "Failed to cache pending payment reference."
            )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A payment for this plan is already pending. "
                "Complete that payment or wait for it to expire."
            ),
            headers={"X-Error-Code": "payment_pending"},
        )

    # ------------------------------------------------------------------
    # 8. Create the pending payment
    # ------------------------------------------------------------------
    reference = f"sub_{secrets.token_urlsafe(16)}"

    payment = SubscriptionPayment(
        firm_id=firm.id,
        plan_id=plan.id,
        payment_method_id=payment_method.id,
        amount=plan.price,
        currency=settings.currency_code,
        payment_reference=reference,
        status="pending",
    )

    db.add(payment)

    await _commit_or_rollback(
        db,
        operation="creating a subscription payment",
    )

    await _refresh_or_raise(
        db,
        payment,
        operation="refreshing a subscription payment",
    )

    # ------------------------------------------------------------------
    # 9. Cache the idempotency key
    # ------------------------------------------------------------------
    try:
        await redis.set(
            idem_key,
            reference,
            ex=IDEMPOTENCY_TTL_SECONDS,
        )

    except Exception:
        logger.exception(
            "Failed to cache idempotency key for payment=%s",
            reference,
        )

    # ------------------------------------------------------------------
    # 10. Initiate payment with the selected provider
    # ------------------------------------------------------------------
    try:
        gateway_response = await initiate_payment(
            provider=provider,
            amount=str(payment.amount),
            currency=payment.currency,
            reference=payment.payment_reference,
        )

    except Exception as exc:
        logger.exception(
            "Payment initiation failed or outcome is uncertain: "
            "reference=%s provider=%s",
            reference,
            provider,
        )

        # Keep the payment pending because a timeout or network error
        # does not prove the provider failed to create the transaction.
        # Reconcile with the provider before marking it failed.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "We could not confirm payment initiation. "
                "Please check the payment status before trying again."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 11. Return the payment state
    # ------------------------------------------------------------------
    gateway_status = gateway_response.get("status", "pending")

    logger.info(
        "Subscription payment initiated: firm_id=%s plan_id=%s "
        "reference=%s provider=%s status=%s",
        firm.id,
        plan.id,
        reference,
        provider,
        gateway_status,
    )

    return {
        "message": (
            "Payment initiated. Complete the payment to activate "
            "your subscription."
        ),
        "payment_reference": reference,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": gateway_status,
        "provider_url": gateway_response.get("provider_url"),
            }






# =============================================================================
# CHECKOUT — Load order details and available payment methods
# =============================================================================

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col


async def get_checkout_details(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> CheckoutResponse:
    """
    Prepare checkout details for an authenticated customer.

    Validates:
        - User is verified and enabled.
        - Customer owns an eligible firm.
        - Plan, tier, and service are active.
        - At least one active payment method is available.

    Does not create a payment or activate a subscription.
    """

    # ------------------------------------------------------------------
    # 1. Validate user state
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Find the customer's firms
    # ------------------------------------------------------------------
    try:
        firms_result = await db.execute(
            select(Firm)
            .where(
                col(Firm.user_id) == current_user.id
            )
            .order_by(col(Firm.created_at))
        )

        firms = firms_result.scalars().all()

    except SQLAlchemyError as exc:
        raise _database_error(
            "loading the customer's firms",
            exc,
        ) from exc

    # Customers can only purchase for firms they own.
    # An administrator should not automatically be allowed to
    # purchase for an unrelated firm through this customer flow.
    active_firm = next(
        (firm for firm in firms if not firm.disabled),
        None,
    )

    if active_firm is None:
        if not firms:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "You need to create a firm before subscribing."
                ),
                headers={"X-Error-Code": "no_firm"},
            )

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "All your firms are disabled. "
                "Please contact admin."
            ),
            headers={"X-Error-Code": "firm_suspended"},
        )

    # ------------------------------------------------------------------
    # 3. Load the selected plan
    # ------------------------------------------------------------------
    plan = await get_plan_by_id(db, plan_id)

    if not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The selected subscription plan is unavailable.",
        )

    # ------------------------------------------------------------------
    # 4. Load and validate the tier
    # ------------------------------------------------------------------
    tier = await get_tier_by_id(db, plan.tier_id)

    if not tier.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The selected subscription tier is unavailable.",
        )

    # ------------------------------------------------------------------
    # 5. Load and validate the service
    # ------------------------------------------------------------------
    try:
        service = await db.get(
            SubscriptionService,
            tier.service_id,
        )

    except SQLAlchemyError as exc:
        raise _database_error(
            "loading the subscription service",
            exc,
        ) from exc

    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The selected subscription service is unavailable.",
        )

    # ------------------------------------------------------------------
    # 6. Load active payment methods
    # ------------------------------------------------------------------
    try:
        payment_methods_result = await db.execute(
            select(PaymentMethods)
            .where(
                col(PaymentMethods.is_active).is_(True)
            )
            .order_by(col(PaymentMethods.name))
        )

        payment_methods = payment_methods_result.scalars().all()

    except SQLAlchemyError as exc:
        raise _database_error(
            "loading available payment methods",
            exc,
        ) from exc

    if not payment_methods:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No payment methods are currently available. "
                "Please try again later."
            ),
        )

    # ------------------------------------------------------------------
    # 7. Build the checkout response
    # ------------------------------------------------------------------
    return CheckoutResponse(
        plan=to_plan_read(plan),
        tier=TierRead.model_validate(tier),
        service=ServiceRead.model_validate(service),
        firm=FirmRead.model_validate(active_firm),
        payment_methods=[
            to_payment_method_read(method)
            for method in payment_methods
        ],
        amount=plan.price,
        currency=settings.currency_code,
    )


async def confirm_subscription_payment(
    data: SubscriptionPaymentConfirm,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Verify a pending payment and activate or extend its subscription.

    The payment and subscription are persisted together.
    """
    guard_user_state(current_user)

    # 1. Lock the payment row to serialize concurrent confirmations.
    try:
        result = await db.execute(
            select(SubscriptionPayment)
            .where(
                col(
                    SubscriptionPayment.payment_reference
                ) == data.payment_reference
            )
            .with_for_update()
        )
        payment = result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error(
            "loading the payment",
            exc,
        ) from exc

    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found.",
        )

    # 2. Verify firm access before revealing payment details.
    firm = await require_firm_access(
        db,
        firm_id=payment.firm_id,
        current_user=current_user,
        require_active=True,
    )

    # 3. Enforce payment state.
    if payment.status == "paid":
        # A paid payment must never activate a second subscription
        # or extend an existing one again.
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

    # 4. Load payment method and verify with the actual provider.
    try:
        payment_method = await db.get(
            PaymentMethods,
            payment.payment_method_id,
        )
    except SQLAlchemyError as exc:
        raise _database_error(
            "loading the payment method",
            exc,
        ) from exc

    if payment_method is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment method not found for this payment.",
        )

    provider = payment_method.provider_code

    try:
        verification = await verify_payment(
            provider=provider,
            reference=payment.payment_reference,
        )
    except Exception as exc:
        logger.exception(
            "Gateway verification failed for reference=%s",
            payment.payment_reference,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment verification is temporarily unavailable.",
        ) from exc

    if verification.get("status") != "paid":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="The payment has not been confirmed by the gateway.",
            headers={"X-Error-Code": "payment_pending"},
        )

    # 5. Load the plan and validate the payment amount.
    plan = await get_plan_by_id(db, payment.plan_id)

    # Do not require the plan to remain active here. A plan might have
    # been deactivated after a legitimate payment was initiated.
    # Payment verification and the recorded purchase determine whether
    # the customer paid successfully.
    tier = await get_tier_by_id(db, plan.tier_id)

    # A paid purchase should not silently activate an unrelated plan.
    if tier.service_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The purchased plan is no longer correctly configured.",
        )

    service_id = tier.service_id

    # Validate gateway amount/currency when the provider returns them.
    # Use the provider's documented response fields for your gateway.
    verified_amount = verification.get("amount")
    verified_currency = verification.get("currency")

    if (
        verified_amount is not None
        and Decimal(str(verified_amount)) != Decimal(str(payment.amount))
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The verified payment amount does not match the purchase.",
        )

    if (
        verified_currency is not None
        and verified_currency != payment.currency
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The verified payment currency does not match the purchase.",
        )

    # 6. Lock or serialize the subscription row before extending it.
    #
    # This helper must use an appropriate row lock or transaction
    # strategy for concurrent purchase confirmations.
    sub = await get_subscription_by_firm_service(
        db,
        payment.firm_id,
        service_id,
    )

    now = utc_now()

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
        base_time = as_utc(sub.subscription_ends_at)
    else:
        base_time = now

    ends_at = add_calendar_months(
        base_time,
        plan.duration_months,
    )

    # 7. Mark payment paid and set subscription fields.
    payment.status = "paid"
    payment.paid_at = now
    db.add(payment)

    if sub is None:
        sub = Subscription(
            firm_id=firm.id,
            service_id=service_id,
            tier_id=tier.id,
            plan_id=plan.id,
            last_payment_id=payment.id,
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
        sub.last_payment_id = payment.id
        sub.is_on_trial = False
        sub.trial_started_at = None
        sub.trial_ends_at = None
        sub.is_active = True
        sub.subscription_ends_at = ends_at
        sub.auto_renew = False

        if not same_plan:
            sub.subscription_started_at = now

        db.add(sub)

    # 8. Commit both database changes together.
    await _commit_or_rollback(
        db,
        operation="confirming a subscription payment",
    )

    await _refresh_or_raise(
        db,
        payment,
        operation="refreshing a payment",
    )
    await _refresh_or_raise(
        db,
        sub,
        operation="refreshing a subscription",
    )

    logger.info(
        "Subscription payment confirmed: reference=%s firm_id=%s "
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







