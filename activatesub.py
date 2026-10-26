

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from api.firm.models import Firm
from api.auth.schemas import ReadUser


  
class SubscriptionPayment(BaseModel, table=True):
    """
    One payment attempt for activating or extending a subscription.

    Typical lifecycle:
        pending -> paid
        pending -> failed
        pending -> cancelled

    A payment can remain pending while awaiting gateway confirmation
    or reconciliation after an uncertain gateway response.
    """

    __tablename__ = "subscription_payments" # type: ignore

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

    # -------------------------------------------------------------------------
    # Relationships to existing records
    # -------------------------------------------------------------------------

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

    # -------------------------------------------------------------------------
    # Idempotency
    # -------------------------------------------------------------------------

    idempotency_key: str = Field(
        sa_column=Column(
            String(100),
            nullable=False,
        )
    )

    # -------------------------------------------------------------------------
    # Payment details
    # -------------------------------------------------------------------------

    # Snapshot of the amount when the purchase was initiated.
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

    # Your application's reference, generated before contacting the gateway.
    payment_reference: str = Field(
        sa_column=Column(
            String(100),
            nullable=False,
            unique=True,
            index=True,
        )
    )

    # Actual transaction reference returned by the payment provider,
    # if the provider supplies one.
    provider_reference: str | None = Field(
        default=None,
        sa_column=Column(
            String(150),
            nullable=True,
            index=True,
        ),
    )

    # Snapshot of the internal provider identifier used for this attempt.
    provider_code: str = Field(
        sa_column=Column(
            String(30),
            nullable=False,
        )
    )

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    status: str = Field(
        default="pending",
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="pending",
        ),
    )

    # -------------------------------------------------------------------------
    # Timestamps
    # -------------------------------------------------------------------------

    paid_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True},
    )

    # -------------------------------------------------------------------------
    # Relationships
    # -------------------------------------------------------------------------

    firm: Optional["Firm"] = Relationship()
    plan: Optional["SubscriptionPlan"] = Relationship()
    payment_method: Optional["PaymentMethods"] = Relationship()









from pydantic import BaseModel, ConfigDict, Field


class SubscriptionPurchase(BaseModel):
    """Initiate a subscription purchase for a firm."""
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(gt=0)
    plan_id: int = Field(gt=0)
    payment_method_id: int = Field(gt=0)

The frontend should generate one idempotency key per purchase attempt and reuse it when retrying that 
same request. It should generate a new key for a genuinely new purchase attempt
    
    # Client-generated UUID or similarly unique request key.
    idempotency_key: str = Field(
        min_length=16,
        max_length=128,
    )





async def activate_subscription(
    data: SubscriptionActivate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Administrator override: activate or extend a subscription
    without a payment record.
    """
    if not _is_admin(current_user):
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
        raise _database_error(
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
        base_time = as_utc(sub.subscription_ends_at)
    else:
        # New subscription, expired subscription, or plan switch.
        base_time = now

    ends_at = add_calendar_months(
        base_time,
        plan.duration_months,
    )

    if sub is None:
        sub = Subscription(
            firm_id=firm.id,
            service_id=service.id,
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

    await _commit_or_rollback(
        db,
        operation="activating a subscription",
    )
    await _refresh_or_raise(
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
        "subscription": _compute_subscription_read(sub),
}





import secrets

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col


async def initiate_subscription_purchase(
    data: SubscriptionPurchase,
    db: AsyncSession,
    redis: Redis,
    current_user: ReadUser,
) -> dict:
    """
    Initiate a user-facing subscription purchase.

    The subscription is not activated here.
    Activation happens only after successful payment verification.
    """
    guard_user_state(current_user)

    idem_key = (
        f"purchase_idem:{current_user.id}:{data.idempotency_key}"
    )

    # 1. Idempotency lookup.
    try:
        cached_reference = await redis.get(idem_key)
    except Exception:
        logger.exception("Redis idempotency read failed.")
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
                "loading an idempotent payment",
                exc,
            ) from exc

        if existing_payment is not None:
            # Do not expose a cached payment unless its firm still
            # belongs to this user or the caller is an administrator.
            await require_firm_access(
                db,
                firm_id=existing_payment.firm_id,
                current_user=current_user,
                require_active=True,
            )

            return {
                "message": "Payment already initiated.",
                "payment_reference": (
                    existing_payment.payment_reference
                ),
                "amount": existing_payment.amount,
                "currency": existing_payment.currency,
                "status": existing_payment.status,
                "provider_url": None,
            }

    # 2. Firm ownership and state.
    firm = await require_firm_access(
        db,
        firm_id=data.firm_id,
        current_user=current_user,
        require_active=True,
    )

    # 3. Plan -> tier -> service.
    plan = await get_plan_by_id(db, data.plan_id)

    if not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found or inactive.",
        )

    tier = await get_tier_by_id(db, plan.tier_id)

    if not tier.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription tier not found or inactive.",
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
            detail="Subscription service not found or inactive.",
        )

    # 4. Payment method.
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
            detail="Payment method not found or inactive.",
        )

    # 5. Check for an existing pending payment.
    existing_pending = await get_pending_payment(
        db,
        firm.id,
        plan.id,
    )

    if existing_pending is not None:
        try:
            await redis.set(
                idem_key,
                existing_pending.payment_reference,
                ex=IDEMPOTENCY_TTL_SECONDS,
            )
        except Exception:
            logger.exception(
                "Redis idempotency write failed."
            )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A payment for this plan is already pending. "
                "Complete it or wait for it to expire."
            ),
            headers={"X-Error-Code": "payment_pending"},
        )

    # 6. Create and persist the pending payment.
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

    # 7. Cache the idempotency key after the payment is committed.
    try:
        await redis.set(
            idem_key,
            reference,
            ex=IDEMPOTENCY_TTL_SECONDS,
        )
    except Exception:
        logger.exception(
            "Redis idempotency write failed for payment %s",
            reference,
        )

    # 8. Initiate payment with the stable internal provider code.
    provider = payment_method.provider_code

    try:
        gateway_response = await initiate_payment(
            provider=provider,
            amount=str(payment.amount),
            currency=payment.currency,
            reference=reference,
        )

    except Exception:
        logger.exception(
            "Gateway initiation failed for payment ref=%s",
            reference,
        )

        # The gateway request may have reached the provider even if
        # the client received an exception. Do not automatically mark
        # it failed unless your gateway integration can establish that
        # the payment was not created.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "We could not confirm the payment initiation. "
                "Please check the payment status before retrying."
            ),
        )

    logger.info(
        "Subscription purchase initiated: firm_id=%s plan_id=%s "
        "reference=%s provider=%s",
        firm.id,
        plan.id,
        reference,
        provider,
    )

    return {
        "message": (
            "Payment initiated. Complete the payment to activate."
        ),
        "payment_reference": reference,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": gateway_response.get("status", "pending"),
        "provider_url": gateway_response.get("provider_url"),
    }



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













