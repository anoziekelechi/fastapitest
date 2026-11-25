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




async def buy_subscription_now(
    data: BuyNowRequest,
    db: AsyncSession,
    current_user: ReadUser,
) -> BuyNowResponse:
    guard_user_state(current_user)

    try:
        # --------------------------------------------------
        # 1. Validate and lock the selected firm
        # --------------------------------------------------
        # can we use our firm_guard here
        firm_result = await db.execute(
            select(Firm)
            .where(
                Firm.id == data.firm_id,
                Firm.user_id == current_user.id,
            )
            .with_for_update()
        )
        firm = firm_result.scalar_one_or_none()
        # validate firm via firm_guard_state
        if firm is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Firm not found.",
            )

        if firm.disabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This firm is disabled.",
            )

        # --------------------------------------------------
        # 2. Validate the selected plan
        # --------------------------------------------------

        plan_result = await db.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.id == data.plan_id,
                SubscriptionPlan.is_active.is_(True),
            )
        )
        plan = plan_result.scalar_one_or_none()

        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found or inactive.",
            )

        # --------------------------------------------------
        # 3. Validate its tier
        # --------------------------------------------------

        tier_result = await db.execute(
            select(SubscriptionTier).where(
                SubscriptionTier.id == plan.tier_id,
                SubscriptionTier.is_active.is_(True),
            )
        )
        tier = tier_result.scalar_one_or_none()

        if tier is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The subscription tier is inactive.",
            )

        # --------------------------------------------------
        # 4. Validate its service
        # --------------------------------------------------

        service_result = await db.execute(
            select(SubscriptionService).where(
                SubscriptionService.id == tier.service_id,
                SubscriptionService.is_active.is_(True),
            )
        )
        service = service_result.scalar_one_or_none()

        if service is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The subscription service is inactive.",
            )

        # --------------------------------------------------
        # 5. Prevent duplicate service for the same firm
        # --------------------------------------------------

        duplicate_result = await db.execute(
            select(OrderItem.id)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.user_id == current_user.id,
                Order.status.in_(UNFINISHED_ORDER_STATUSES),
                OrderItem.firm_id == firm.id,
                OrderItem.service_id == service.id,
            )
            .limit(1)
        )

        if duplicate_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Service already in cart for this firm.",
            )

        # --------------------------------------------------
        # 6. Find an editable order or create one
        # --------------------------------------------------

        order_result = await db.execute(
            select(Order)
            .where(
                Order.user_id == current_user.id,
                Order.status == "pending_checkout",
            )
            .order_by(Order.id.desc())
            .with_for_update()
            .limit(1)
        )
        order = order_result.scalar_one_or_none()

        if order is None:
            order = Order(
                order_number=generate_order_number(),
                user_id=current_user.id,
                status="pending_checkout",
            )
            db.add(order)
            await db.flush()

        # --------------------------------------------------
        # 7. Save the purchase-time snapshots
        # --------------------------------------------------

        price = Decimal(str(plan.price))
        discount = Decimal("0.00")
        total = price - discount
        currency = "LRD"

        order_item = OrderItem(
            order_id=order.id,
            firm_id=firm.id,
            service_id=service.id,
            tier_id=tier.id,
            plan_id=plan.id,
            service_name=service.name,
            tier_name=tier.name,
            plan_name=plan.name,
            duration_months=plan.duration_months,
            unit_price=price,
            discount_amount=discount,
            total_amount=total,
            currency=currency,
        )

        db.add(order_item)
        await db.flush()

        response = BuyNowResponse(
            order_number=order.order_number,
            order_item_id=order_item.id,
            firm_id=firm.id,
            firm_name=firm.name,
            service_name=service.name,
            tier_name=tier.name,
            plan_name=plan.name,
            duration_months=plan.duration_months,
            amount=total,
            currency=currency,
        )

        await db.commit()
        return response

    except HTTPException:
        await db.rollback()
        raise

    except SQLAlchemyError:
        await db.rollback()
        logger.exception(
            "Failed to add subscription to cart for user_id=%s",
            current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to add this subscription to your cart.",
            )




class BuyNowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(gt=0)
    plan_id: int = Field(gt=0)
    #FirmListRead:

my suggestion and questions are in  CheckoutDetailsRead 
we includes AvailablePaymentsMethods from payment schemas
class CheckoutDetailsRead(BaseModel):
    order_number: str
    items: list[CheckoutItemRead]
    total_amount: Decimal
    currency: str

    # Reuse the existing public response schema.
    payment_methods: AvailablePaymentMethodsRead

why not do the same in Buynowrequest
class BuyNowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(gt=0)
    plan_id: int = Field(gt=0)
    #FirmListRead:
    
    
Again i already have a function that check firm state and ownersship why not use it as i been doing in other of my 
logics instead of writting this 



if firm is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Firm not found.",
            )

        if firm.disabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This firm is disabled.",
            )
