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
        raise _database_error(
            "reading subscriptions for a firm",
            exc,
        ) from exc

    return [
        _compute_subscription_read(sub)
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
        raise _database_error(
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

    firm = await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=True,
    )

    sub = await get_subscription_by_firm_service(
        db,
        firm.id,
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

    await _commit_or_rollback(
        db,
        operation="updating a subscription",
    )
    await _refresh_or_raise(
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
        "subscription": _compute_subscription_read(sub),
    }


from datetime import timedelta


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
            service_id = _service_id(service)

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



from datetime import datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class SubscriptionActivate(BaseModel):
    """Administrator-only activation override."""
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(gt=0)
    service_id: int = Field(gt=0)
    tier_id: int = Field(gt=0)
    plan_id: int = Field(gt=0)


class SubscriptionUpdate(BaseModel):
    """Only auto_renew is user-editable."""
    model_config = ConfigDict(extra="forbid")

    auto_renew: bool


class SubscriptionRead(BaseModel):
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
    """Status of one firm subscription for one service."""
    model_config = ConfigDict(extra="forbid")

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


class SubscriptionPurchase(BaseModel):
    """Initiate a purchase for a firm."""
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(gt=0)
    plan_id: int = Field(gt=0)
    payment_method_id: int = Field(gt=0)
    idempotency_key: str = Field(
        min_length=16,
        max_length=128,
    )


class SubscriptionPurchaseResponse(BaseModel):
    message: str
    payment_reference: str
    amount: Decimal
    currency: str
    status: str
    provider_url: str | None = None


class SubscriptionPaymentConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_reference: str = Field(min_length=10)


class SubscriptionPaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    plan_id: int
    payment_method_id: int
    amount: Decimal
    currency: str
    payment_reference: str
    status: str
    paid_at: datetime | None = None
    created_at: datetime


class SubscriptionPaymentConfirmedResponse(BaseModel):
    message: str
    payment: SubscriptionPaymentRead
    subscription: SubscriptionRead


class ActivateSubscriptionResponse(BaseModel):
    message: str
    subscription: SubscriptionRead


class UpdateSubscriptionResponse(BaseModel):
    message: str
    subscription: SubscriptionRead








# api/models/orders.py

from datetime import UTC, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, Relationship

from api.models.base import BaseModel


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

    firm: Optional["Firm"] = Relationship()
    service: Optional["SubscriptionService"] = Relationship()
    tier: Optional["SubscriptionTier"] = Relationship()
    plan: Optional["SubscriptionPlan"] = Relationship()






# api/models/subscription_payments.py

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, Relationship

from api.models.base import BaseModel


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
        default="LRD",
        sa_column=Column(
            String(3),
            nullable=False,
            server_default="LRD",
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

    firm: Optional["Firm"] = Relationship()
    order: Optional["Order"] = Relationship()
    plan: Optional["SubscriptionPlan"] = Relationship()
    payment_method: Optional["PaymentMethods"] = Relationship()




