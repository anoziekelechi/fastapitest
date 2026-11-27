Argument of type "str" cannot be assigned to parameter "country" of type "Country" in function "__init__"
  "str" is not assignable to "Country"PylancereportArgumentType



@router.post(
    "/add",
    response_model=CreateFirmResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a firm (authenticated)",
)
async def create(
    db: DBDep,
    current_user: CurrentUser,
    name: Annotated[str, Form(min_length=2, max_length=200)],
    country: Annotated[str, Form(min_length=2, max_length=200)],
    deals_on: Annotated[str, Form(min_length=10, max_length=2000)],
    registration_number: Annotated[
        str | None, Form(max_length=50)
    ] = None,
    firm_logo_key: UploadFile | None = File(None),
):
    data = FirmCreate(
        name=name,
        deals_on=deals_on,
        registration_number=registration_number,
        country=country,
    )
    return await create_firm(
        data=data,
        db=db,
        current_user=current_user,
        firm_logo_key=firm_logo_key,
    )


# 3. Database Model: base_currency is REQUIRED (not None)
class Organization(SQLModel, table=True):
    __tablename__ = "organizations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(max_length=255)
    country: Country = Field(default=Country.LIBERIA)
    base_currency: Currency = Field(nullable=False)  # Required in PostgreSQL


# 4. API Input Schema: User only provides name & country
class OrganizationCreate(SQLModel):
    name: str
    country: Country = Country.LIBERIA


# 5. Endpoint: Automatically fills base_currency from org.country
@router.post("/organizations", response_model=Organization)
def create_organization(payload: OrganizationCreate, session: Session = Depends(get_session)):
    org = Organization(
        name=payload.name,
        country=payload.country,
        base_currency=COUNTRY_BASE_CURRENCY[payload.country],  # Auto-filled!
    )
    session.add(org)
    session.commit()
    session.refresh(org)
    return org








class SubscriptionPayment(BaseModel, table=True):
    """
    One payment attempt for an entire subscription order.

    An order may contain multiple firms and multiple subscription plans.
    """

    __tablename__ = "subscription_payments"  # type: ignore

    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "idempotency_key",
            name="uq_subscription_payment_order_idempotency",
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

    order_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("orders.id", ondelete="RESTRICT"),
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

    idempotency_key: str = Field(
        sa_column=Column(
            String(100),
            nullable=False,
        )
    )

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

    status: str = Field(
        default="pending",
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="pending",
            index=True,
        ),
    )

    paid_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True},
    )

    order: Optional["Order"] = Relationship(
        back_populates="payments",
    )

    payment_method: Optional["PaymentMethods"] = Relationship(
        back_populates="subscription_payments",
    )







async def require_firm_access(
    db: AsyncSession,
    *,
    firm_id: int,
    current_user: ReadUser,
    require_active: bool = True,
    for_update: bool = False,
) -> Firm:
    """
    Load a firm and verify the caller can access it.

    Steps:
        1. Load the firm.
        2. Return 404 if it does not exist.
        3. Verify ownership or admin permission.
        4. Optionally reject disabled firms.
        5. Optionally acquire a row-level lock.

    Does not check user state.
    """
    from api.core.logging import logger

    try:
        if for_update:
            result = await db.execute(
                select(Firm)
                .where(Firm.id == firm_id)
                .with_for_update()
            )
            firm = result.scalar_one_or_none()
        else:
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
    """
    Add a subscription purchase to the user's pending checkout order.

    Rules:
        - A user must select the firm for the purchase.
        - The selected firm must belong to the user or be accessible
          to an administrator.
        - Disabled firms cannot make new purchases.
        - Only active plans, tiers and services can be purchased.
        - The same firm cannot add the same service to another
          unfinished order.
        - Different firms can purchase the same service.
        - Different services can be added for the same firm.
        - Purchases are saved as OrderItem snapshots.
        - No payment is initiated here.
    """

    guard_user_state(current_user)

    try:
        # --------------------------------------------------
        # 1. Validate and lock the selected firm
        # --------------------------------------------------

        firm = await require_firm_access(
            db,
            firm_id=data.firm_id,
            current_user=current_user,
            require_active=True,
            for_update=True,
        )

        # --------------------------------------------------
        # 2. Load and validate the selected plan
        # --------------------------------------------------

        plan_result = await db.execute(
            select(SubscriptionPlan)
            .where(
                SubscriptionPlan.id == data.plan_id,
                SubscriptionPlan.is_active.is_(True),
            )
            .with_for_update()
        )

        plan = plan_result.scalar_one_or_none()

        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found or inactive.",
            )

        # --------------------------------------------------
        # 3. Load and validate the plan's tier
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
                detail="The subscription tier is inactive or unavailable.",
            )

        # --------------------------------------------------
        # 4. Load and validate the tier's service
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
                detail="The subscription service is inactive or unavailable.",
            )

        # --------------------------------------------------
        # 5. Prevent duplicate service purchases in unfinished
        #    orders for the same firm
        # --------------------------------------------------

        duplicate_result = await db.execute(
            select(OrderItem.id)
            .join(
                Order,
                Order.id == OrderItem.order_id,
            )
            .where(
                Order.user_id == current_user.id,
                Order.status.in_(UNFINISHED_ORDER_STATUSES),
                OrderItem.firm_id == firm.id,
                OrderItem.service_id == service.id,
            )
            .limit(1)
        )

        duplicate_item_id = duplicate_result.scalar_one_or_none()

        if duplicate_item_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"The {service.name} service is already in "
                    f"an unfinished order for firm '{firm.name}'."
                ),
            )

        # --------------------------------------------------
        # 6. Find an existing editable order
        # --------------------------------------------------
        # Only pending_checkout orders can receive new items.
        # Never modify an order that is already pending_payment.

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

        # --------------------------------------------------
        # 7. Create an order if no editable order exists
        # --------------------------------------------------

        if order is None:
            order = Order(
                order_number=generate_order_number(),
                user_id=current_user.id,
                status="pending_checkout",
            )

            db.add(order)
            await db.flush()

        # --------------------------------------------------
        # 8. Capture purchase-time price and details
        # --------------------------------------------------
        # These values are saved so that later plan price/name
        # changes cannot alter an existing checkout.

        price = Decimal(str(plan.price)).quantize(
            Decimal("0.01")
        )

        discount = Decimal("0.00")

        total = price - discount

        # Use the application's configured currency if you have
        # one. This assumes your subscription prices are in LRD.
        currency = "LRD"

        # --------------------------------------------------
        # 9. Create the order item
        # --------------------------------------------------

        order_item = OrderItem(
            order_id=order.id,
            firm_id=firm.id,
            service_id=service.id,
            tier_id=tier.id,
            plan_id=plan.id,

            # Purchase-time snapshots
            service_name=service.name,
            tier_name=tier.name,
            plan_name=plan.name,
            duration_months=plan.duration_months,

            # Purchase-time financial values
            unit_price=price,
            discount_amount=discount,
            total_amount=total,
            currency=currency,
        )

        db.add(order_item)

        await db.flush()

        # --------------------------------------------------
        # 10. Prepare response
        # --------------------------------------------------

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

        # --------------------------------------------------
        # 11. Commit
        # --------------------------------------------------

        await db.commit()

        return response

    except HTTPException:
        await db.rollback()
        raise

    except SQLAlchemyError as exc:
        await db.rollback()

        logger.exception(
            "Database error while adding subscription to cart "
            "for user_id=%s, firm_id=%s, plan_id=%s",
            current_user.id,
            data.firm_id,
            data.plan_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to add this subscription to your cart.",
        ) from exc

    except Exception as exc:
        await db.rollback()

        logger.exception(
            "Unexpected error while adding subscription to cart "
            "for user_id=%s, firm_id=%s, plan_id=%s",
            current_user.id,
            data.firm_id,
            data.plan_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        ) from exc







