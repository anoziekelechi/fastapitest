#VALIDATOR
def normalize_user_message(value: str, field_name: str = "Message") -> str:
    """
    Normalize text blocks (messages, details, deals) by collapsing multiple spaces,
    trimming padding, and ensuring dynamic error reporting for empty fields.
    Keeps original letter casing.
    """
    if not value or not value.strip():
        raise ValueError(f"{field_name} cannot be empty")
        
    # Collapse multiple consecutive spaces/tabs, but preserve newlines
    cleaned = re.sub(r"[ \t]+", " ", value.strip())
    
    return cleaned
# SCHEMAS


class ServiceCreate(BaseModel):
    """Admin creates a service."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=50)
    description: str = Field(..., min_length=10)
    trial_days: int = Field(default=30, gt=0)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str,info: FieldValidationInfo) -> str:
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_payment_option(v, field_name=display_name)
       
    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, v: str,info: FieldValidationInfo) -> str:
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_user_message(v, field_name=display_name).capitalize()
   


class ServiceUpdate(BaseModel):
    """Admin updates a service. Name is not editable."""
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    trial_days: int | None = Field(default=None, gt=0)
    is_active: bool | None = None
    
    
    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, v: str | None, info: FieldValidationInfo) -> str | None:
        if v is None:
            return None
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_user_message(v, field_name=display_name).capitalize()



class ServiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    slug: str | None = None
    is_active: bool
    trial_days: int
    created_at: datetime
    updated_at: datetime


# =============================================================================
# TIER
# =============================================================================

class TierCreate(BaseModel):
    """Admin creates a tier for a service."""
    model_config = ConfigDict(extra="forbid")

    service_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=2, max_length=50)
    description: str

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str,info: FieldValidationInfo) -> str:
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_payment_option(v, field_name=display_name)

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, v: str, info: FieldValidationInfo) -> str | None:
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_user_message(v, field_name=display_name).capitalize()

   


class TierUpdate(BaseModel):
    """Admin updates a tier. Name is not editable."""
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    is_active: bool | None = None

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, v: str | None, info: FieldValidationInfo) -> str | None:
        if v is None:
            return None
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_user_message(v, field_name=display_name).capitalize()



class TierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_id: int
    name: str
    slug: str | None = None
    description: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TierWithPlansRead(BaseModel):
    """Tier with all its plans — for pricing pages."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_id: int
    name: str
    slug: str | None = None
    description: str | None = None
    is_active: bool
    plans: list["PlanRead"] = []


# =============================================================================
# PLAN
# =============================================================================

class PlanCreate(BaseModel):
    """Admin creates a plan for a tier."""
    model_config = ConfigDict(extra="forbid")

    tier_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=2, max_length=50)
    description: str | None = None
    duration_days: int = Field(..., gt=0)
    price: Decimal = Field(..., gt=Decimal("0"))
    
    
    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str,info: FieldValidationInfo) -> str:
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_payment_option(v, field_name=display_name)


    @field_validator("description", mode="before")
    def validate_description(cls, v: str | None, info: FieldValidationInfo) -> str | None:
        if v is None:
            return None
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_user_message(v, field_name=display_name).capitalize()



class PlanUpdate(BaseModel):
    """Admin updates a plan. Name and tier_id are not editable."""
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    duration_days: int | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, gt=Decimal("0"))
    is_active: bool | None = None

    
    @field_validator("description", mode="before")
    def validate_description(cls, v: str | None, info: FieldValidationInfo) -> str | None:
        if v is None:
            return None
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_user_message(v, field_name=display_name).capitalize()

   

class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tier_id: int
    name: str
    slug: str | None = None
    description: str | None = None
    duration_days: int
    price: Decimal
    currency:str
    is_active: bool
    created_at: datetime
    updated_at: datetime


# =============================================================================
# SUBSCRIPTION
# =============================================================================

class SubscriptionActivate(BaseModel):
    """Activate (or extend) a paid subscription for a firm."""
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(..., gt=0)
    service_id: int = Field(..., gt=0)
    tier_id: int = Field(..., gt=0)
    plan_id: int = Field(..., gt=0)


class SubscriptionUpdate(BaseModel):
    """Only auto_renew is user-editable."""
    model_config = ConfigDict(extra="forbid")

    auto_renew: bool | None = None


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    service_id: int
    tier_id: int | None = None
    plan_id: int | None = None

    is_on_trial: bool
    trial_started_at: datetime
    trial_ends_at: datetime | None = None

    is_active: bool
    subscription_started_at: datetime | None = None
    subscription_ends_at: datetime | None = None
    auto_renew: bool

    created_at: datetime
    updated_at: datetime

    # Computed by the service layer
    days_remaining: int | None = None
    status_label: str | None = None
    can_access: bool = False


class SubscriptionStatusRead(BaseModel):
    """Quick status check for one firm + one service."""
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


class ServiceWithTiersRead(BaseModel):
    """Service with its full tier/plan tree — for the pricing page."""
    id: int
    name: str
    description: str
    trial_days: int
    tiers: list[TierWithPlansRead] = []


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreateServiceResponse(BaseModel):
    message: str
    service: ServiceRead


class UpdateServiceResponse(BaseModel):
    message: str
    service: ServiceRead


class CreateTierResponse(BaseModel):
    message: str
    tier: TierRead


class UpdateTierResponse(BaseModel):
    message: str
    tier: TierRead


class CreatePlanResponse(BaseModel):
    message: str
    plan: PlanRead


class UpdatePlanResponse(BaseModel):
    message: str
    plan: PlanRead


class ActivateSubscriptionResponse(BaseModel):
    message: str
    subscription: SubscriptionRead


class UpdateSubscriptionResponse(BaseModel):
    message: str
    subscription: SubscriptionRead


class MessageResponse(BaseModel):
    message: str


#MODELS



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
        sa_column=Column(String(200), nullable=True, unique=True, index=True)
    )
    description: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True)
    )
    duration_days: int = Field(
        sa_column=Column(Integer, nullable=False)
        # monthly=30, quarterly=90, biannual=180, annual=365
    )
    price: float = Field(
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


class Subscription(BaseModel, table=True):
    """
    Subscription record per FIRM per SERVICE.

    Key design:
        - Subscription is at FIRM level not Branch level
        - If Firm A has active receipt subscription,
          ALL branches of Firm A can issue receipts
        - Branches just check: firm.has_active_subscription("receipt")

    Trial:
        - Auto-created when firm is created
        - Duration from service.trial_days
        - After trial, firm must subscribe to continue
    """
    __tablename__ = "subscriptions" # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "firm_id",
            "service_id",
            name="unique_subscription_per_firm_service"
        ),
    )

    # FK to Firm (FIRM level - not branch)
    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    # FK to SubscriptionService
    service_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_services.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    # FK to SubscriptionTier (null during trial)
    tier_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("subscription_tiers.id", ondelete="SET NULL"),
            nullable=True,
        )
    )

    # FK to SubscriptionPlan (null during trial)
    plan_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("subscription_plans.id", ondelete="SET NULL"),
            nullable=True,
        )
    )

    # Trial tracking
    is_on_trial: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true")
    )
    trial_started_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": False}
    )
    trial_ends_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True}
    )

    # Paid subscription tracking
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true")
    )
    subscription_started_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True}
    )
    subscription_ends_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True}
    )

    auto_renew: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false")
    )

    # Relationships
    firm: Optional["Firm"] = Relationship(
        back_populates="subscriptions"
    )
    service: Optional["SubscriptionService"] = Relationship(
        back_populates="subscriptions"
    )
    tier: Optional["SubscriptionTier"] = Relationship(
        back_populates="subscriptions"
    )
    plan: Optional["SubscriptionPlan"] = Relationship(
        back_populates="subscriptions"
  )



  
  
  
  
   
class Team(BaseModel,table=True):# type: ignore
    __tablename__ = "teams" # type: ignore
    user_id: int = Field (
        default= None,
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable= True,
            index=True
        )
    )
    position:str = Field(
        sa_column=Column(String(50),nullable=False)
    )
    education:str = Field(
        sa_column=Column(String(200),nullable=False)
    )
    
    user:Optional ["User"] = Relationship(back_populates="team", sa_relationship_kwargs={"uselist": False})

    


#LOGICS


