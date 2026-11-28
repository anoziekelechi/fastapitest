class Firm(SQLModel, table=True):
    __tablename__ = "firms"

    __table_args__ = (
        UniqueConstraint(
            "country", "name",
            name="uq_firms_country_name",
        ),
    )

    # ... existing fields ...

    name: str = Field(
        sa_column=Column(String(200), nullable=False, index=True)
        # no unique=True — the compound constraint handles it
    )

    country: Country = Field(
        sa_column=Column(
            SAEnum(Country, name="country_enum"),
            nullable=False,
            index=True,
        ),
    )

    base_currency: Currency = Field(
        sa_column=Column(
            SAEnum(Currency, name="currency_enum"),
            nullable=False,
            server_default=Currency.USD.value,
        ),
    )



from api.core.country import Country, Currency


class FirmCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=200)
    registration_number: str | None = Field(
        default=None, min_length=2, max_length=50,
    )
    country: Country                                       # <-- new, required
    deals_on: str = Field(..., min_length=10, max_length=2000)

    @field_validator("name", mode="before")
    @classmethod
    def validate_firm_name(cls, v: str) -> str:
        return normalize_firm_name(v)

    @field_validator("deals_on", mode="before")
    @classmethod
    def validate_deal_on_fields(
        cls, v: str, info: FieldValidationInfo,
    ) -> str:
        raw_name = info.field_name or "field"
        display_name = raw_name.replace("_", " ").capitalize()
        return normalize_user_message(v, field_name=display_name)


class FirmUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=2, max_length=200)
    registration_number: str | None = Field(
        default=None, min_length=2, max_length=50,
    )
    deals_on: str | None = Field(None, min_length=10, max_length=2000)

    # Country is intentionally NOT mutable. Moving a firm between
    # countries would:
    #   - break the base_currency (firm would owe taxes in a
    #     currency it was never registered under)
    #   - break receipts (issued under the old country's tax rules)
    #   - create audit ambiguity
    # If the business needs country migration, that's a support
    # action with its own audit trail — not a PATCH field.

    @field_validator("name", mode="before")
    @classmethod
    def validate_firmname(cls, v: str | None) -> str | None:
        return normalize_firm_name(v) if v else None

    @field_validator("deals_on", mode="before")
    @classmethod
    def validate_deal_on_fields(
        cls, v: str | None, info: FieldValidationInfo,
    ) -> str | None:
        if v is None:
            return None
        raw_name = info.field_name or "field"
        display_name = raw_name.replace("_", " ").capitalize()
        return normalize_user_message(v, field_name=display_name)


class FirmRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    registration_number: str | None = None
    slug: str | None = None
    deals_on: str
    country: Country                 # <-- new
    base_currency: Currency          # <-- new
    disabled: bool
    created_at: datetime
    updated_at: datetime

    branch_count: int = 0
    receipt_count: int = 0
    branches: list[BranchRead] = []
    firm_logo_url: str | None = None


class FirmListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    registration_number: str | None = None
    slug: str | None = None
    deals_on: str
    country: Country                 # <-- new
    base_currency: Currency          # <-- new
    disabled: bool
    created_at: datetime
    updated_at: datetime

    branch_count: int = 0
    firm_logo_url: str | None = None



#new helper
async def get_firm_by_name_and_country(
    db: AsyncSession,
    name: str,
    country: Country,
    *,
    exclude_firm_id: int | None = None,
) -> Firm | None:
    """
    Fetch a firm by (country, name).

    This is the canonical uniqueness check now — firm names are
    unique per country, not globally. Two firms can share a name
    as long as they're in different countries.
    """
    normalized = normalize_firm_name(name)
    try:
        stmt = select(Firm).where(
            col(Firm.name) == normalized,
            col(Firm.country) == country,
        )
        if exclude_firm_id is not None:
            stmt = stmt.where(col(Firm.id) != exclude_firm_id)
        result = await db.execute(stmt)
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch firm name=%s country=%s",
            normalized, country.value,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        ) from exc


async def create_firm(
    data: FirmCreate,
    db: AsyncSession,
    firm_logo_key: UploadFile | None,
    current_user: ReadUser,
) -> dict:
    guard_user_state(current_user)

    if current_user.id is None:
        logger.error("Current user has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create firm. Please try again.",
        )
    owner_id = current_user.id

    # ------------------------------------------------------------------
    # 1. Pre-check: (country, name) uniqueness
    # ------------------------------------------------------------------
    existing = await get_firm_by_name_and_country(
        db, name=data.name, country=data.country,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A firm named '{data.name}' is already registered "
                f"in {data.country.value}."
            ),
        )

    # ------------------------------------------------------------------
    # 2. Pre-check: registration_number uniqueness (unchanged)
    # ------------------------------------------------------------------
    if data.registration_number is not None:
        existing_reg = await get_firm_by_registration_number(
            db, data.registration_number,
        )
        if existing_reg is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Registration number "
                    f"'{data.registration_number}' is already "
                    f"registered."
                ),
            )

    # ------------------------------------------------------------------
    # 3. Upload the optional logo (unchanged)
    # ------------------------------------------------------------------
    new_logo_key: str | None = None
    if firm_logo_key is not None:
        try:
            new_logo_key = await handle_file_update(
                file=firm_logo_key,
                current_key=None,
                prefix=FIRM_LOGO,
                max_size=FIRM_LOGO_MAX_SIZE,
            )
            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the firm logo.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "Firm logo upload failed for name=%s", data.name,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload firm logo.",
            ) from exc

    # ------------------------------------------------------------------
    # 4. Prepare the model — derive base_currency from country
    # ------------------------------------------------------------------
    firm = Firm(
        user_id=owner_id,
        name=data.name,
        registration_number=data.registration_number,
        country=data.country,
        base_currency=COUNTRY_BASE_CURRENCY[data.country],
        slug=generate_slug(data.name),
        deals_on=data.deals_on,
        firm_logo_key=new_logo_key,
        disabled=False,
    )

    db.add(firm)

    # ------------------------------------------------------------------
    # 5. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()
    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after firm IntegrityError: name=%s",
                data.name,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up logo after IntegrityError: "
                    "name=%s, logo_key=%s", data.name, new_logo_key,
                )

        logger.warning(
            "Firm creation violated a database integrity constraint: "
            "name=%s, country=%s, rollback_succeeded=%s",
            data.name, data.country.value, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A firm named '{data.name}' is already registered "
                f"in {data.country.value}, or the registration "
                f"number conflicts."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after firm flush error: name=%s",
                data.name,
            )
        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up logo after flush error: "
                    "name=%s, logo_key=%s", data.name, new_logo_key,
                )
        logger.exception(
            "Database error while flushing new firm: "
            "name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create firm. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    except Exception as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error: name=%s",
                data.name,
            )
        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up logo after unexpected error: "
                    "name=%s, logo_key=%s", data.name, new_logo_key,
                )
        logger.exception(
            "Unexpected error while flushing new firm: "
            "name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating firm.",
        ) from exc

    # ------------------------------------------------------------------
    # 6. Commit (unchanged)
    # ------------------------------------------------------------------
    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: name=%s",
                data.name,
            )
        logger.exception(
            "Firm commit failed or has an uncertain outcome. "
            "Preserving logo for reconciliation: "
            "name=%s, new_logo_key=%s", data.name, new_logo_key,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The firm creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 7. Refresh (best-effort) + return (unchanged)
    # ------------------------------------------------------------------
    try:
        await db.refresh(firm)
    except Exception:
        logger.exception(
            "Failed to refresh firm after create: name=%s", data.name,
        )

    fid = firm_id_of(firm)

    logger.info(
        "Firm created: firm_id=%s, name=%s, country=%s, "
        "base_currency=%s, owner_id=%s",
        fid, firm.name, firm.country.value,
        firm.base_currency.value, owner_id,
    )

    read = to_firm_detail(firm, branches=[], receipt_counts={})
    read.branch_count = 0
    read.receipt_count = 0

    return {
        "message": f"Firm '{firm.name}' created successfully",
        "firm": read,
                    }





#update
# --- name ---
if data.name is not None and data.name != firm.name:
    existing = await get_firm_by_name_and_country(
        db,
        name=data.name,
        country=firm.country,       # <-- the firm's existing country
        exclude_firm_id=fid,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A firm named '{data.name}' is already registered "
                f"in {firm.country.value}."
            ),
        )

    old_slug = firm.slug
    firm.name = data.name
    firm.slug = generate_slug(data.name)
    updated_fields.append("name")
    logger.info(
        "Firm slug updated: '%s' -> '%s'", old_slug, firm.slug,
    )





#api/core/country


class Country(str, Enum):
    LIBERIA = "Liberia"
    GHANA = "Ghana"
    NIGERIA = "Nigeria"


class Currency(str, Enum):
    USD = "USD"
    LRD = "LRD"
    GHS = "GHS"
    NGN = "NGN"


# 1. Auto-assigned base reporting currency per country
COUNTRY_BASE_CURRENCY: dict[Country, Currency] = {
    Country.LIBERIA: Currency.USD,
    Country.GHANA: Currency.GHS,
    Country.NIGERIA: Currency.NGN,
}

"""
then i updated the firm to have a country 
"""
 firm = Firm(
        user_id=owner_id,
        name=data.name,
        registration_number=data.registration_number,
        country=data.country,#new
        base_currency=COUNTRY_BASE_CURRENCY[data.country], #new
        slug=generate_slug(data.name),
        deals_on=data.deals_on,
        firm_logo_key=new_logo_key,
        disabled=False,
    )

    db.add(firm)
i need to write a check in create_firm so 2 firms with same name name cant create firm 
raise an error if abc limited already registerd in Liberia then block another firm from liberia from using same abc limited
so lets update the create and update also we can add country in FIRMRead


# 2. Allowed payment currencies on receipts (Liberia still supports BOTH USD and LRD)
COUNTRY_ALLOWED_CURRENCIES: dict[Country, list[Currency]] = {
    Country.LIBERIA: [Currency.USD, Currency.LRD],
    Country.GHANA: [Currency.GHS],
    Country.NIGERIA: [Currency.NGN],
}

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







