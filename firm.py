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



