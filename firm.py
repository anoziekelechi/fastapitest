

class SubscriptionTier(SQLModel, table=True):
    __tablename__ = "subscription_tiers"

    # ... existing fields ...

    is_trial_tier: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,
            nullable=False,
            default=False,
            index=True,
        ),
    )



class TierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(None, max_length=2000)

    # Optional override. If omitted (None), the service logic
    # decides based on the tier name. If provided, it wins.
    is_trial_tier: bool | None = Field(
        default=None,
        description=(
            "Whether this tier is trialable. "
            "If omitted, defaults to True for tiers named 'Starter'."
        ),
    )

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        return normalize_field(v)





class TierUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(None, max_length=2000)
    is_active: bool | None = None
    is_trial_tier: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        for field in ("description", "is_active", "is_trial_tier"):
            if (
                field in self.model_fields_set
                and getattr(self, field) is None
            ):
                raise ValueError(f"{field} cannot be null.")
        return self



class TierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_id: int
    name: str
    description: str | None = None
    slug: str
    is_active: bool
    is_trial_tier: bool          # <-- new
    created_at: datetime
    updated_at: datetime



# api/subscriptions/logics/tier.py

TRIAL_TIER_NAME = "STARTER"


def default_trial_tier_flag(name: str) -> bool:
    """
    Default policy: only tiers named 'STARTER' are trialable.

    Names are normalized to UPPERCASE by the schema validator before
    reaching this function, so we compare against the same form.
    We still .upper() the input defensively in case this is called
    from a context that skipped Pydantic (tests, scripts, migrations).
    """
    return name.strip().upper() == TRIAL_TIER_NAME




async def create_tier(
    service_slug: str,
    data: TierCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a tier under a service.

    `is_trial_tier` resolution:
        1. If the caller supplied `is_trial_tier`, use it.
        2. Otherwise, default to True when `name == "Starter"`.
    """
    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    # ------------------------------------------------------------------
    # 2. Pre-check: (service_id, name) uniqueness
    # ------------------------------------------------------------------
    existing = await get_tier_by_service_and_name(
        db, service_id=sid, name=data.name,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A tier named '{data.name}' already exists "
                f"for this service."
            ),
        )

    # ------------------------------------------------------------------
    # 3. Resolve is_trial_tier
    # ------------------------------------------------------------------
    is_trial_tier = (
        data.is_trial_tier
        if data.is_trial_tier is not None
        else default_trial_tier_flag(data.name)
    )

    # ------------------------------------------------------------------
    # 4. Prepare the model
    # ------------------------------------------------------------------
    tier = SubscriptionTier(
        service_id=sid,
        name=data.name,
        description=data.description,
        slug=generate_slug(f"{service.slug or service.name}-{data.name}"),
        is_active=True,
        is_trial_tier=is_trial_tier,
    )

    db.add(tier)

    # ------------------------------------------------------------------
    # 5. Flush → commit → refresh (unchanged from before)
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
                "Rollback failed after tier IntegrityError: "
                "service_id=%s, name=%s", sid, data.name,
            )
        logger.warning(
            "Tier creation violated a database integrity constraint: "
            "service_id=%s, name=%s, rollback_succeeded=%s",
            sid, data.name, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A tier named '{data.name}' (or its slug) already "
                f"exists."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after tier flush error: service_id=%s",
                sid,
            )
        logger.exception(
            "Database error while flushing new tier: "
            "service_id=%s, rollback_succeeded=%s",
            sid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create tier. "
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
                "Rollback failed after unexpected tier flush error: "
                "service_id=%s", sid,
            )
        logger.exception(
            "Unexpected error while flushing new tier: "
            "service_id=%s, rollback_succeeded=%s",
            sid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating tier.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "service_id=%s, name=%s", sid, data.name,
            )
        logger.exception(
            "Tier commit failed or has an uncertain outcome: "
            "service_id=%s, name=%s", sid, data.name,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The tier creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(tier)
    except Exception:
        logger.exception(
            "Failed to refresh tier after create: service_id=%s", sid,
        )

    logger.info(
        "Tier '%s' (slug=%s, is_trial_tier=%s) created for "
        "service_id=%s by user_id=%s",
        tier.name, tier.slug, tier.is_trial_tier, sid, current_user.id,
    )

    return {
        "message": "Subscription tier created successfully.",
        "tier": to_tier_read(tier),
    }



async def update_tier(
    service_slug: str,
    tier_slug: str,
    data: TierUpdate,
    db: AsyncSession,
) -> dict:
    """
    Update a tier under a service.

    Mutable:
        - description
        - is_active
        - is_trial_tier

    Immutable:
        - name        (URLs and plan references key on it)
        - slug        (derived from service + name at creation)
        - service_id  (a tier never moves between services)
    """
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    tier = await get_tier_by_service_and_slug(
        db, service_id=sid, tier_slug=tier_slug,
    )
    assert tier is not None
    tid = tier_id_of(tier)

    # Reject empty payload
    if all(
        v is None
        for v in (data.description, data.is_active, data.is_trial_tier)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    updated_fields: list[str] = []

    if (
        data.description is not None
        and data.description != tier.description
    ):
        tier.description = data.description
        updated_fields.append("description")

    if (
        data.is_active is not None
        and data.is_active != tier.is_active
    ):
        tier.is_active = data.is_active
        updated_fields.append("is_active")

    if (
        data.is_trial_tier is not None
        and data.is_trial_tier != tier.is_trial_tier
    ):
        tier.is_trial_tier = data.is_trial_tier
        updated_fields.append("is_trial_tier")

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ... flush → commit → refresh (same as before)
    ...













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
# you introduced SubscriptionStatusLabel

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
    # you added 
    status_label

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
    status_label: str vs your own usesSubscriptionStatusLabel
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



