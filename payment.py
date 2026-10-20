
# api/subscriptions/models.py
"""Subscription models."""
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    Column, String, Integer, ForeignKey,
    Boolean, Text, Numeric, UniqueConstraint
)
from sqlmodel import Field, Relationship
from api.core.base import BaseModel, utc_now

if TYPE_CHECKING:
    from api.firms.models import Firm


class SubscriptionService(BaseModel, table=True):
    """
    Available subscription services stored in DB.
    Admin adds new services here - no code changes needed.

    Examples:
        receipt → Receipt generation service
        debtors → Debtor tracking service
    """
    __tablename__ = "subscription_services"

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
    __tablename__ = "subscription_tiers"
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
    description: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True)
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
            monthly   → 30 days,  NGN 5,000
            quarterly → 90 days,  NGN 12,000
            biannual  → 180 days, NGN 20,000
            annual    → 365 days, NGN 35,000

        pro tier:
            monthly   → 30 days,  NGN 10,000
            quarterly → 90 days,  NGN 25,000
            biannual  → 180 days, NGN 45,000
            annual    → 365 days, NGN 70,000
    """
    __tablename__ = "subscription_plans"
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
    currency: str = Field(
        default="NGN",
        sa_column=Column(String(3), nullable=False, server_default="NGN")
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
    __tablename__ = "subscriptions"
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




# api/firms/models.py
"""Firm and Branch models."""
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    Column, String, Integer, ForeignKey, Text,
    UniqueConstraint
)
from sqlmodel import Field, Relationship
from api.core.base import BaseModel

if TYPE_CHECKING:
    from api.users.models import User
    from api.subscriptions.models import Subscription
    from api.receipts.models import Receipt


class Firm(BaseModel, table=True):
    """
    Company/firm profile.
    One user can own many firms.
    A firm can have many branches.

    Subscription is at FIRM level:
        Firm A subscribes → all Firm A branches get access
        No need to subscribe per branch
    """
    __tablename__ = "firms"

    # FK to User - NOT unique (user can own many firms)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    name: str = Field(
        sa_column=Column(String(200), nullable=False, unique=True, index=True)
        # e.g. "ABC Business Entity", "XYZ and Sons Trading Company"
    )

    slug: str | None = Field(
        default=None,
        sa_column=Column(String(250), nullable=True, unique=True, index=True)
    )

    # Government registration number e.g. CAC in Nigeria
    registration_number: str | None = Field(
        default=None,
        sa_column=Column(String(100), nullable=True, unique=True)
    )

    deals_on: str = Field(
        sa_column=Column(Text, nullable=False)
        # What the firm trades/deals in
    )

    # Relationships
    owner: Optional["User"] = Relationship(back_populates="firms")
    branches: list["Branch"] = Relationship(back_populates="firm")
    subscriptions: list["Subscription"] = Relationship(  # ✅ Firm level
        back_populates="firm"
    )
    receipts: list["Receipt"] = Relationship(back_populates="firm")


class Branch(BaseModel, table=True):
    """
    Physical branch of a firm.
    A firm can have many branches.

    Access to services is determined by FIRM subscription:
        Branch checks firm.subscriptions to see what is active
        Branch itself has NO subscription - firm handles it
    """
    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint(
            "firm_id",
            "branch_no",
            name="unique_branch_no_per_firm"
            # Same branch_no cannot appear twice in same firm
            # Different firms can have "HQ" or "001"
        ),
    )

    # FK to Firm
    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    branch_no: str = Field(
        sa_column=Column(String(50), nullable=False)
        # e.g. "001", "HQ", "LAGOS-01", "ABUJA-02"
    )

    slug: str | None = Field(
        default=None,
        sa_column=Column(String(200), nullable=True, unique=True, index=True)
    )

    address: str = Field(
        sa_column=Column(Text, nullable=False)
    )

    phone_number: str = Field(
        sa_column=Column(String(20), nullable=False)
    )

    # Relationships
    firm: Optional["Firm"] = Relationship(back_populates="branches")
    receipts: list["Receipt"] = Relationship(back_populates="branch")
