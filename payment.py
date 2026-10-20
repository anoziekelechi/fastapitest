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



# api/subscriptions/logics.py
"""Subscription business logic."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.core.settings import get_settings
from api.core.slug import generate_slug
from api.models.subscriptions import (
    Subscription,
    SubscriptionPlan,
    SubscriptionService,
    SubscriptionTier,
)
from api.subscriptions.schemas import (
    PlanCreate,
    PlanRead,
    PlanUpdate,
    ServiceCreate,
    ServiceRead,
    ServiceUpdate,
    ServiceWithTiersRead,
    SubscriptionActivate,
    SubscriptionRead,
    SubscriptionStatusRead,
    SubscriptionUpdate,
    TierCreate,
    TierRead,
    TierUpdate,
    TierWithPlansRead,
)
from api.users.schemas import ReadUser

logger = logging.getLogger(__name__)
settings = get_settings()


# =============================================================================
# RESPONSE BUILDERS
# =============================================================================

def to_plan_read(plan: SubscriptionPlan) -> PlanRead:
    """
    Build a PlanRead with currency resolved from platform settings.

    `currency` is a platform-wide setting (single-country mode),
    not a per-plan DB column.
    """
    read = PlanRead.model_validate(plan)
    return read.model_copy(
        update={"currency": settings.currency_code}
    )


def _compute_subscription_read(sub: Subscription) -> SubscriptionRead:
    """
    Build a SubscriptionRead with computed fields:
        days_remaining, status_label, can_access.
    """
    now = datetime.now(timezone.utc)

    days_remaining: int | None = None
    status_label: str
    can_access: bool

    if sub.is_on_trial:
        if sub.trial_ends_at:
            delta = sub.trial_ends_at - now
            days_remaining = max(0, delta.days)
        else:
            days_remaining = 0

        if sub.is_active and days_remaining > 0:
            status_label = "trial"
            can_access = True
        else:
            status_label = "trial_expired"
            can_access = False

    else:
        if sub.subscription_ends_at:
            delta = sub.subscription_ends_at - now
            days_remaining = max(0, delta.days)
        else:
            days_remaining = 0

        if sub.is_active and days_remaining > 0:
            status_label = "active"
            can_access = True
        else:
            status_label = "expired"
            can_access = False

    read = SubscriptionRead.model_validate(sub)
    return read.model_copy(
        update={
            "days_remaining": days_remaining,
            "status_label": status_label,
            "can_access": can_access,
        }
    )


# =============================================================================
# ID NARROWING
# =============================================================================

def _service_id(service: SubscriptionService) -> int:
    if service.id is None:
        raise HTTPException(500, "Failed to load service. Please try again.")
    return service.id


def _tier_id(tier: SubscriptionTier) -> int:
    if tier.id is None:
        raise HTTPException(500, "Failed to load tier. Please try again.")
    return tier.id


def _plan_id(plan: SubscriptionPlan) -> int:
    if plan.id is None:
        raise HTTPException(500, "Failed to load plan. Please try again.")
    return plan.id


# =============================================================================
# LOOKUP HELPERS
# =============================================================================

async def get_service_by_slug(
    db: AsyncSession,
    slug: str,
) -> SubscriptionService | None:
    try:
        result = await db.execute(
            select(SubscriptionService).where(
                col(SubscriptionService.slug) == slug.strip().lower()
            )
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch service slug=%s", slug)
        raise HTTPException(500, "Failed to load service. Please try again.")


async def get_tier_by_id(
    db: AsyncSession,
    tier_id: int,
) -> SubscriptionTier | None:
    try:
        return await db.get(SubscriptionTier, tier_id)
    except Exception:
        logger.exception("Failed to fetch tier id=%s", tier_id)
        raise HTTPException(500, "Failed to load tier. Please try again.")


async def get_plan_by_id(
    db: AsyncSession,
    plan_id: int,
) -> SubscriptionPlan | None:
    try:
        return await db.get(SubscriptionPlan, plan_id)
    except Exception:
        logger.exception("Failed to fetch plan id=%s", plan_id)
        raise HTTPException(500, "Failed to load plan. Please try again.")


async def get_subscription_by_firm_service(
    db: AsyncSession,
    firm_id: int,
    service_id: int,
) -> Subscription | None:
    try:
        result = await db.execute(
            select(Subscription).where(
                col(Subscription.firm_id) == firm_id,
                col(Subscription.service_id) == service_id,
            )
        )
        return result.scalars().first()
    except Exception:
        logger.exception(
            "Failed to fetch subscription firm_id=%s service_id=%s",
            firm_id,
            service_id,
        )
        raise HTTPException(
            500, "Failed to load subscription. Please try again."
        )


# =============================================================================
# SERVICE — CREATE
# =============================================================================

async def create_service(
    data: ServiceCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin-only. Enforced by route via `admin: AdminUser`."""

    try:
        existing = (
            await db.execute(
                select(SubscriptionService).where(
                    col(SubscriptionService.name) == data.name,
                )
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check existing service name=%s", data.name
        )
        raise HTTPException(500, "Failed to create service. Please try again.")

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Service '{data.name}' already exists",
        )

    service = SubscriptionService(
        name=data.name,
        description=data.description,
        slug=generate_slug(data.name),
        trial_days=data.trial_days,
        is_active=True,
    )

    try:
        db.add(service)
        await db.commit()
        await db.refresh(service)
    except Exception:
        await db.rollback()
        logger.exception("Failed to create service name=%s", data.name)
        raise HTTPException(500, "Failed to create service. Please try again.")

    logger.info(
        "Service '%s' (slug=%s) created by admin user_id=%s",
        service.name,
        service.slug,
        current_user.id,
    )

    return {
        "message": f"Service '{service.name}' created successfully",
        "service": ServiceRead.model_validate(service),
    }


# =============================================================================
# SERVICE — READ
# =============================================================================

async def read_services(
    db: AsyncSession,
    current_user: ReadUser | None,
    skip: int = 0,
    limit: int = 100,
) -> list[ServiceRead]:
    """
    List services.

    Public callers see only active services. Admins see all.
    """
    try:
        query = select(SubscriptionService).order_by(
            col(SubscriptionService.name)
        )
        if current_user is None or not current_user.is_admin:
            query = query.where(
                col(SubscriptionService.is_active) == True  # noqa: E712
            )
        query = query.offset(skip).limit(limit)

        result = await db.execute(query)
        services = result.scalars().all()
    except Exception:
        logger.exception("Failed to list services")
        raise HTTPException(500, "Failed to load services. Please try again.")

    return [ServiceRead.model_validate(s) for s in services]


async def read_service_by_slug(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None,
) -> ServiceRead:
    service = await get_service_by_slug(db, slug)
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{slug}' not found",
        )

    if (
        not service.is_active
        and (current_user is None or not current_user.is_admin)
    ):
        raise HTTPException(404, f"Service '{slug}' not found")

    return ServiceRead.model_validate(service)


async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None,
) -> ServiceWithTiersRead:
    """
    Service + tiers + plans — for the pricing page.

    Public callers see only active tiers and plans.
    """
    service = await get_service_by_slug(db, slug)
    if not service:
        raise HTTPException(404, f"Service '{slug}' not found")

    if (
        not service.is_active
        and (current_user is None or not current_user.is_admin)
    ):
        raise HTTPException(404, f"Service '{slug}' not found")

    is_admin = current_user is not None and current_user.is_admin
    sid = _service_id(service)

    try:
        tiers_result = await db.execute(
            select(SubscriptionTier)
            .where(col(SubscriptionTier.service_id) == sid)
            .order_by(col(SubscriptionTier.name))
        )
        tiers = tiers_result.scalars().all()

        tier_reads: list[TierWithPlansRead] = []
        for tier in tiers:
            if not tier.is_active and not is_admin:
                continue

            plans_result = await db.execute(
                select(SubscriptionPlan)
                .where(
                    col(SubscriptionPlan.tier_id) == _tier_id(tier)
                )
                .order_by(col(SubscriptionPlan.duration_days))
            )
            plans = plans_result.scalars().all()

            visible_plans = (
                plans if is_admin
                else [p for p in plans if p.is_active]
            )

            tier_reads.append(
                TierWithPlansRead.model_validate(tier).model_copy(
                    update={
                        "plans": [
                            to_plan_read(p) for p in visible_plans
                        ],
                    }
                )
            )
    except Exception:
        logger.exception(
            "Failed to build service pricing tree for slug=%s", slug
        )
        raise HTTPException(500, "Failed to load service. Please try again.")

    return ServiceWithTiersRead(
        id=sid,
        name=service.name,
        description=service.description,
        trial_days=service.trial_days,
        tiers=tier_reads,
    )


# =============================================================================
# SERVICE — UPDATE
# =============================================================================

async def update_service(
    slug: str,
    data: ServiceUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin-only. Enforced by route."""

    service = await get_service_by_slug(db, slug)
    if not service:
        raise HTTPException(404, f"Service '{slug}' not found")

    if (
        data.description is None
        and data.trial_days is None
        and data.is_active is None
    ):
        raise HTTPException(
            400, "At least one field must be provided for update"
        )

    updated: list[str] = []

    if data.description is not None and data.description != service.description:
        service.description = data.description
        updated.append("description")

    if data.trial_days is not None and data.trial_days != service.trial_days:
        service.trial_days = data.trial_days
        updated.append("trial_days")

    if data.is_active is not None and data.is_active != service.is_active:
        service.is_active = data.is_active
        updated.append("is_active")

    if not updated:
        raise HTTPException(
            400,
            "No changes detected - all supplied values are identical "
            "to the current ones",
        )

    try:
        db.add(service)
        await db.commit()
        await db.refresh(service)
    except Exception:
        await db.rollback()
        logger.exception("Failed to update service slug=%s", slug)
        raise HTTPException(500, "Failed to update service. Please try again.")

    logger.info(
        "Service '%s' updated by admin user_id=%s (fields=%s)",
        service.name,
        current_user.id,
        ",".join(updated),
    )

    return {
        "message": f"Service '{service.name}' updated successfully",
        "service": ServiceRead.model_validate(service),
    }


# =============================================================================
# SERVICE — DELETE
# =============================================================================

async def delete_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin-only. Blocked if active subscriptions reference this service."""

    service = await get_service_by_slug(db, slug)
    if not service:
        raise HTTPException(404, f"Service '{slug}' not found")

    try:
        sub_count: int = (
            await db.execute(
                select(func.count())
                .select_from(Subscription)
                .where(
                    col(Subscription.service_id) == _service_id(service)
                )
            )
        ).scalar() or 0
    except Exception:
        logger.exception(
            "Failed to count subscriptions for service id=%s",
            _service_id(service),
        )
        raise HTTPException(500, "Failed to delete service. Please try again.")

    if sub_count > 0:
        raise HTTPException(
            409,
            f"This service has {sub_count} "
            f"{'subscription' if sub_count == 1 else 'subscriptions'} "
            "and cannot be deleted. Deactivate it instead.",
        )

    name = service.name

    try:
        await db.delete(service)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete service slug=%s", slug)
        raise HTTPException(500, "Failed to delete service. Please try again.")

    logger.info(
        "Service '%s' deleted by admin user_id=%s", name, current_user.id
    )

    return {"message": f"Service '{name}' deleted successfully"}


# =============================================================================
# TIER — CREATE
# =============================================================================

async def create_tier(
    data: TierCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin-only."""

    service = await db.get(SubscriptionService, data.service_id)
    if not service:
        raise HTTPException(
            404, f"Service with ID {data.service_id} not found"
        )

    try:
        existing = (
            await db.execute(
                select(SubscriptionTier).where(
                    col(SubscriptionTier.service_id) == data.service_id,
                    col(SubscriptionTier.name) == data.name,
                )
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check existing tier name=%s service_id=%s",
            data.name,
            data.service_id,
        )
        raise HTTPException(500, "Failed to create tier. Please try again.")

    if existing:
        raise HTTPException(
            409,
            f"Tier '{data.name}' already exists for service "
            f"'{service.name}'",
        )

    tier = SubscriptionTier(
        service_id=data.service_id,
        name=data.name,
        description=data.description,
        slug=generate_slug(
            f"{service.slug or service.name}-{data.name}"
        ),
        is_active=True,
    )

    try:
        db.add(tier)
        await db.commit()
        await db.refresh(tier)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create tier name=%s service_id=%s",
            data.name,
            data.service_id,
        )
        raise HTTPException(500, "Failed to create tier. Please try again.")

    logger.info(
        "Tier '%s' created for service '%s' by admin user_id=%s",
        tier.name,
        service.name,
        current_user.id,
    )

    return {
        "message": (
            f"Tier '{tier.name}' created successfully "
            f"for service '{service.name}'"
        ),
        "tier": TierRead.model_validate(tier),
    }


# =============================================================================
# TIER — READ
# =============================================================================

async def read_tiers_for_service(
    service_slug: str,
    db: AsyncSession,
    current_user: ReadUser | None,
) -> list[TierRead]:
    service = await get_service_by_slug(db, service_slug)
    if not service:
        raise HTTPException(404, f"Service '{service_slug}' not found")

    is_admin = current_user is not None and current_user.is_admin

    try:
        query = (
            select(SubscriptionTier)
            .where(
                col(SubscriptionTier.service_id) == _service_id(service)
            )
            .order_by(col(SubscriptionTier.name))
        )
        if not is_admin:
            query = query.where(
                col(SubscriptionTier.is_active) == True  # noqa: E712
            )

        result = await db.execute(query)
        tiers = result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to list tiers for service slug=%s", service_slug
        )
        raise HTTPException(500, "Failed to load tiers. Please try again.")

    return [TierRead.model_validate(t) for t in tiers]


async def read_tier_by_id(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser | None,
) -> TierRead:
    tier = await get_tier_by_id(db, tier_id)
    if not tier:
        raise HTTPException(404, f"Tier id={tier_id} not found")

    is_admin = current_user is not None and current_user.is_admin
    if not tier.is_active and not is_admin:
        raise HTTPException(404, f"Tier id={tier_id} not found")

    return TierRead.model_validate(tier)


# =============================================================================
# TIER — UPDATE
# =============================================================================

async def update_tier(
    tier_id: int,
    data: TierUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin-only."""

    tier = await get_tier_by_id(db, tier_id)
    if not tier:
        raise HTTPException(404, f"Tier id={tier_id} not found")

    if data.description is None and data.is_active is None:
        raise HTTPException(
            400, "At least one field must be provided for update"
        )

    updated: list[str] = []

    if data.description is not None and data.description != tier.description:
        tier.description = data.description
        updated.append("description")

    if data.is_active is not None and data.is_active != tier.is_active:
        tier.is_active = data.is_active
        updated.append("is_active")

    if not updated:
        raise HTTPException(
            400,
            "No changes detected - all supplied values are identical "
            "to the current ones",
        )

    try:
        db.add(tier)
        await db.commit()
        await db.refresh(tier)
    except Exception:
        await db.rollback()
        logger.exception("Failed to update tier id=%s", tier_id)
        raise HTTPException(500, "Failed to update tier. Please try again.")

    logger.info(
        "Tier id=%s updated by admin user_id=%s (fields=%s)",
        tier_id,
        current_user.id,
        ",".join(updated),
    )

    return {
        "message": f"Tier '{tier.name}' updated successfully",
        "tier": TierRead.model_validate(tier),
    }


# =============================================================================
# TIER — DELETE
# =============================================================================

async def delete_tier(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin-only. Blocked if any subscription references this tier."""

    tier = await get_tier_by_id(db, tier_id)
    if not tier:
        raise HTTPException(404, f"Tier id={tier_id} not found")

    try:
        sub_count: int = (
            await db.execute(
                select(func.count())
                .select_from(Subscription)
                .where(col(Subscription.tier_id) == tier_id)
            )
        ).scalar() or 0
    except Exception:
        logger.exception(
            "Failed to count subscriptions for tier id=%s", tier_id
        )
        raise HTTPException(500, "Failed to delete tier. Please try again.")

    if sub_count > 0:
        raise HTTPException(
            409,
            f"This tier has {sub_count} "
            f"{'subscription' if sub_count == 1 else 'subscriptions'} "
            "and cannot be deleted. Deactivate it instead.",
        )

    name = tier.name

    try:
        await db.delete(tier)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete tier id=%s", tier_id)
        raise HTTPException(500, "Failed to delete tier. Please try again.")

    logger.info(
        "Tier '%s' deleted by admin user_id=%s", name, current_user.id
    )

    return {"message": f"Tier '{name}' deleted successfully"}


# =============================================================================
# PLAN — CREATE
# =============================================================================

async def create_plan(
    data: PlanCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin-only."""

    tier = await get_tier_by_id(db, data.tier_id)
    if not tier:
        raise HTTPException(
            404, f"Tier with ID {data.tier_id} not found"
        )

    try:
        existing = (
            await db.execute(
                select(SubscriptionPlan).where(
                    col(SubscriptionPlan.tier_id) == data.tier_id,
                    col(SubscriptionPlan.name) == data.name,
                )
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check existing plan name=%s tier_id=%s",
            data.name,
            data.tier_id,
        )
        raise HTTPException(500, "Failed to create plan. Please try again.")

    if existing:
        raise HTTPException(
            409,
            f"Plan '{data.name}' already exists for tier '{tier.name}'",
        )

    plan = SubscriptionPlan(
        tier_id=data.tier_id,
        name=data.name,
        description=data.description,
        duration_days=data.duration_days,
        price=data.price,
        slug=generate_slug(
            f"{tier.slug or tier.name}-{data.name}"
        ),
        is_active=True,
    )

    try:
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create plan name=%s tier_id=%s",
            data.name,
            data.tier_id,
        )
        raise HTTPException(500, "Failed to create plan. Please try again.")

    logger.info(
        "Plan '%s' created for tier '%s' by admin user_id=%s",
        plan.name,
        tier.name,
        current_user.id,
    )

    return {
        "message": (
            f"Plan '{plan.name}' created successfully "
            f"for tier '{tier.name}'"
        ),
        "plan": to_plan_read(plan),
    }


# =============================================================================
# PLAN — READ
# =============================================================================

async def read_plans_for_tier(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser | None,
) -> list[PlanRead]:
    tier = await get_tier_by_id(db, tier_id)
    if not tier:
        raise HTTPException(404, f"Tier id={tier_id} not found")

    is_admin = current_user is not None and current_user.is_admin

    try:
        query = (
            select(SubscriptionPlan)
            .where(col(SubscriptionPlan.tier_id) == tier_id)
            .order_by(col(SubscriptionPlan.duration_days))
        )
        if not is_admin:
            query = query.where(
                col(SubscriptionPlan.is_active) == True  # noqa: E712
            )

        result = await db.execute(query)
        plans = result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to list plans for tier id=%s", tier_id
        )
        raise HTTPException(500, "Failed to load plans. Please try again.")

    return [to_plan_read(p) for p in plans]


async def read_plan_by_id(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser | None,
) -> PlanRead:
    plan = await get_plan_by_id(db, plan_id)
    if not plan:
        raise HTTPException(404, f"Plan id={plan_id} not found")

    is_admin = current_user is not None and current_user.is_admin
    if not plan.is_active and not is_admin:
        raise HTTPException(404, f"Plan id={plan_id} not found")

    return to_plan_read(plan)


# =============================================================================
# PLAN — UPDATE
# =============================================================================

async def update_plan(
    plan_id: int,
    data: PlanUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin-only."""

    plan = await get_plan_by_id(db, plan_id)
    if not plan:
        raise HTTPException(404, f"Plan id={plan_id} not found")

    if all(
        v is None
        for v in (
            data.description,
            data.duration_days,
            data.price,
            data.is_active,
        )
    ):
        raise HTTPException(
            400, "At least one field must be provided for update"
        )

    updated: list[str] = []

    if data.description is not None and data.description != plan.description:
        plan.description = data.description
        updated.append("description")

    if (
        data.duration_days is not None
        and data.duration_days != plan.duration_days
    ):
        plan.duration_days = data.duration_days
        updated.append("duration_days")

    if data.price is not None and data.price != plan.price:
        plan.price = data.price
        updated.append("price")

    if data.is_active is not None and data.is_active != plan.is_active:
        plan.is_active = data.is_active
        updated.append("is_active")

    if not updated:
        raise HTTPException(
            400,
            "No changes detected - all supplied values are identical "
            "to the current ones",
        )

    try:
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
    except Exception:
        await db.rollback()
        logger.exception("Failed to update plan id=%s", plan_id)
        raise HTTPException(500, "Failed to update plan. Please try again.")

    logger.info(
        "Plan id=%s updated by admin user_id=%s (fields=%s)",
        plan_id,
        current_user.id,
        ",".join(updated),
    )

    return {
        "message": f"Plan '{plan.name}' updated successfully",
        "plan": to_plan_read(plan),
    }


# =============================================================================
# PLAN — DELETE
# =============================================================================

async def delete_plan(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin-only. Blocked if any subscription references this plan."""

    plan = await get_plan_by_id(db, plan_id)
    if not plan:
        raise HTTPException(404, f"Plan id={plan_id} not found")

    try:
        sub_count: int = (
            await db.execute(
                select(func.count())
                .select_from(Subscription)
                .where(col(Subscription.plan_id) == plan_id)
            )
        ).scalar() or 0
    except Exception:
        logger.exception(
            "Failed to count subscriptions for plan id=%s", plan_id
        )
        raise HTTPException(500, "Failed to delete plan. Please try again.")

    if sub_count > 0:
        raise HTTPException(
            409,
            f"This plan has {sub_count} "
            f"{'subscription' if sub_count == 1 else 'subscriptions'} "
            "and cannot be deleted. Deactivate it instead.",
        )

    name = plan.name

    try:
        await db.delete(plan)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete plan id=%s", plan_id)
        raise HTTPException(500, "Failed to delete plan. Please try again.")

    logger.info(
        "Plan '%s' deleted by admin user_id=%s", name, current_user.id
    )

    return {"message": f"Plan '{name}' deleted successfully"}


# =============================================================================
# SUBSCRIPTION — ACTIVATE
# =============================================================================

async def activate_subscription(
    data: SubscriptionActivate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Activate or extend a paid subscription for a firm.

    Admin-only.

    Cross-entity validation:
        - Firm exists
        - Service exists and is active
        - Tier exists, is active, and belongs to service
        - Plan exists, is active, and belongs to tier
    """

    from api.models.users import Firm  # adjust import path if needed

    try:
        firm = await db.get(Firm, data.firm_id)
    except Exception:
        logger.exception("Failed to load firm id=%s", data.firm_id)
        raise HTTPException(500, "Failed to load firm. Please try again.")

    if not firm:
        raise HTTPException(
            404, f"Firm with ID {data.firm_id} not found"
        )

    try:
        service = await db.get(SubscriptionService, data.service_id)
    except Exception:
        logger.exception(
            "Failed to load service id=%s", data.service_id
        )
        raise HTTPException(500, "Failed to load service. Please try again.")

    if not service or not service.is_active:
        raise HTTPException(
            404,
            f"Service with ID {data.service_id} not found or inactive",
        )

    tier = await get_tier_by_id(db, data.tier_id)
    if not tier or not tier.is_active:
        raise HTTPException(
            404, f"Tier with ID {data.tier_id} not found or inactive"
        )

    if tier.service_id != data.service_id:
        raise HTTPException(
            400,
            f"Tier '{tier.name}' does not belong to service "
            f"'{service.name}'",
        )

    plan = await get_plan_by_id(db, data.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(
            404, f"Plan with ID {data.plan_id} not found or inactive"
        )

    if plan.tier_id != data.tier_id:
        raise HTTPException(
            400,
            f"Plan '{plan.name}' does not belong to tier '{tier.name}'",
        )

    sub = await get_subscription_by_firm_service(
        db, data.firm_id, data.service_id
    )

    now = datetime.now(timezone.utc)
    ends_at = now + timedelta(days=plan.duration_days)

    if sub is None:
        sub = Subscription(
            firm_id=data.firm_id,
            service_id=data.service_id,
            tier_id=data.tier_id,
            plan_id=data.plan_id,
            is_on_trial=False,
            trial_started_at=now,
            trial_ends_at=None,
            is_active=True,
            subscription_started_at=now,
            subscription_ends_at=ends_at,
            auto_renew=False,
        )
    else:
        same_plan = (
            sub.tier_id == data.tier_id
            and sub.plan_id == data.plan_id
            and not sub.is_on_trial
            and sub.is_active
            and sub.subscription_ends_at
            and sub.subscription_ends_at > now
        )

        if same_plan:
            ends_at = sub.subscription_ends_at + timedelta(
                days=plan.duration_days
            )
            sub.subscription_ends_at = ends_at
        else:
            sub.tier_id = data.tier_id
            sub.plan_id = data.plan_id
            sub.is_on_trial = False
            sub.trial_ends_at = None
            sub.is_active = True
            sub.subscription_started_at = now
            sub.subscription_ends_at = ends_at

    try:
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to activate subscription firm_id=%s service_id=%s",
            data.firm_id,
            data.service_id,
        )
        raise HTTPException(
            500, "Failed to activate subscription. Please try again."
        )

    logger.info(
        "Subscription activated for firm_id=%s service_id=%s "
        "tier_id=%s plan_id=%s by admin user_id=%s",
        data.firm_id,
        data.service_id,
        data.tier_id,
        data.plan_id,
        current_user.id,
    )

    return {
        "message": (
            f"Subscription activated for '{service.name}' until "
            f"{ends_at.strftime('%Y-%m-%d')}"
        ),
        "subscription": _compute_subscription_read(sub),
    }


# =============================================================================
# SUBSCRIPTION — READ
# =============================================================================

async def read_subscriptions_for_firm(
    firm_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> list[SubscriptionRead]:
    """Owner of the firm, or admin."""

    from api.models.users import Firm

    try:
        firm = await db.get(Firm, firm_id)
    except Exception:
        logger.exception("Failed to load firm id=%s", firm_id)
        raise HTTPException(500, "Failed to load firm. Please try again.")

    if not firm:
        raise HTTPException(404, f"Firm with ID {firm_id} not found")

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            403,
            "You can only view subscriptions for your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    try:
        result = await db.execute(
            select(Subscription)
            .where(col(Subscription.firm_id) == firm_id)
            .order_by(col(Subscription.service_id))
        )
        subs = result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to list subscriptions for firm_id=%s", firm_id
        )
        raise HTTPException(
            500, "Failed to load subscriptions. Please try again."
        )

    return [_compute_subscription_read(s) for s in subs]


async def read_subscription_status(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> SubscriptionStatusRead:
    """Owner of the firm, or admin."""

    from api.models.users import Firm

    try:
        firm = await db.get(Firm, firm_id)
    except Exception:
        logger.exception("Failed to load firm id=%s", firm_id)
        raise HTTPException(500, "Failed to load firm. Please try again.")

    if not firm:
        raise HTTPException(404, f"Firm with ID {firm_id} not found")

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            403,
            "You can only view subscriptions for your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    service = await db.get(SubscriptionService, service_id)
    if not service:
        raise HTTPException(
            404, f"Service with ID {service_id} not found"
        )

    sub = await get_subscription_by_firm_service(db, firm_id, service_id)
    if not sub:
        raise HTTPException(
            404,
            f"No subscription found for firm {firm_id} "
            f"on service '{service.name}'",
        )

    tier = (
        await get_tier_by_id(db, sub.tier_id) if sub.tier_id else None
    )
    plan = (
        await get_plan_by_id(db, sub.plan_id) if sub.plan_id else None
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


# =============================================================================
# SUBSCRIPTION — UPDATE (auto_renew only)
# =============================================================================

async def update_subscription(
    firm_id: int,
    service_id: int,
    data: SubscriptionUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Owner of the firm, or admin. Only `auto_renew` is editable."""

    from api.models.users import Firm

    try:
        firm = await db.get(Firm, firm_id)
    except Exception:
        logger.exception("Failed to load firm id=%s", firm_id)
        raise HTTPException(500, "Failed to load firm. Please try again.")

    if not firm:
        raise HTTPException(404, f"Firm with ID {firm_id} not found")

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            403,
            "You can only update subscriptions for your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    sub = await get_subscription_by_firm_service(db, firm_id, service_id)
    if not sub:
        raise HTTPException(
            404,
            f"No subscription found for firm {firm_id} on service "
            f"{service_id}",
        )

    if data.auto_renew is None:
        raise HTTPException(
            400, "At least one field must be provided for update"
        )

    if data.auto_renew == sub.auto_renew:
        raise HTTPException(
            400,
            "No changes detected - auto_renew is already set to "
            f"{sub.auto_renew}",
        )

    sub.auto_renew = data.auto_renew

    try:
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to update subscription firm_id=%s service_id=%s",
            firm_id,
            service_id,
        )
        raise HTTPException(
            500, "Failed to update subscription. Please try again."
        )

    logger.info(
        "Subscription auto_renew=%s for firm_id=%s service_id=%s "
        "by user_id=%s",
        sub.auto_renew,
        firm_id,
        service_id,
        current_user.id,
    )

    return {
        "message": (
            f"Auto-renew {'enabled' if sub.auto_renew else 'disabled'} "
            f"successfully"
        ),
        "subscription": _compute_subscription_read(sub),
    }


# =============================================================================
# TRIAL CREATION — called from create_firm
# =============================================================================

async def create_trial_subscriptions_for_firm(
    db: AsyncSession,
    firm_id: int,
) -> None:
    """
    Create trial subscriptions for all active services for a new firm.

    Called from `create_firm` in `api.users.logics`. Not exposed via any
    route — this is a system operation.

    Idempotent: skips services the firm already has a subscription for.
    """
    try:
        services_result = await db.execute(
            select(SubscriptionService).where(
                col(SubscriptionService.is_active) == True  # noqa: E712
            )
        )
        services = services_result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load active services for trial creation firm_id=%s",
            firm_id,
        )
        return

    now = datetime.now(timezone.utc)

    for service in services:
        existing = await get_subscription_by_firm_service(
            db, firm_id, _service_id(service)
        )
        if existing:
            continue

        trial_ends_at = now + timedelta(days=service.trial_days)

        sub = Subscription(
            firm_id=firm_id,
            service_id=_service_id(service),
            is_on_trial=True,
            trial_started_at=now,
            trial_ends_at=trial_ends_at,
            is_active=True,
            auto_renew=False,
        )
        db.add(sub)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create trial subscriptions for firm_id=%s", firm_id
                  )


