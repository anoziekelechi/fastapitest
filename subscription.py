
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







