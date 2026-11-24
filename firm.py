#FIRM MODELS

#from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Column, Text, Boolean, DateTime,String,Integer, ForeignKey,Numeric
from api.core.base import BaseModel, utc_now
from sqlmodel import Field,Relationship,UniqueConstraint
from pydantic import  EmailStr
from pydantic_extra_types.phone_numbers import PhoneNumber
from typing import List, Optional
from hashlib import md5

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from api.models.payments import SubscriptionPayment
    from api.models.receipts import Receipt
    from api.models.users import User
    from api.models.orders import Order
    from api.models.subscriptions import Subscription


class Firm(BaseModel, table=True):
    """
    Company/firm profile.
    One user can own many firms.
    A firm can have many branches.

    Subscription is at FIRM level:
        Firm A subscribes → all Firm A branches get access
        No need to subscribe per branch
    """
    __tablename__ = "firms" # type: ignore

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

    # firm_phone_number: str = Field(
    #     sa_column=Column(String(20), nullable=False)
    # )
    firm_logo_key: str | None = Field(
    default=None,
    sa_column=Column(
        String(500),
        nullable=True,
    ),
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
    
    disabled: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,nullable=False,server_default="false"
        )
    )

    # Relationships
    owner: Optional["User"] = Relationship(back_populates="firms")
    branches: list["Branch"] = Relationship(back_populates="firm")
    subscriptions: list["Subscription"] = Relationship(  # ✅ Firm level
        back_populates="firm"
    )
    receipts: list["Receipt"] = Relationship(back_populates="firm")
    
    orders:list["Order"] = Relationship(back_populates="firm")
    subscription_payments:list["SubscriptionPayment"] = Relationship(back_populates="firm")



class Branch(BaseModel, table=True):
    """
    Physical branch of a firm.
    A firm can have many branches.

    Access to services is determined by FIRM subscription:
        Branch checks firm.subscriptions to see what is active
        Branch itself has NO subscription - firm handles it
    """
    __tablename__ = "branches" # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "firm_id",
            "branch_no",
            name="unique_branch_no_per_firm"
            # Same branch_no cannot appear twice in same firm
            # Different firms can have "HQ" or "001"
        ),
        
         UniqueConstraint(
            "firm_id",
            "slug",
            name="unique_slug_per_firm"
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
        # e.g. "001", "HQ", "MONROVIA-01", "BUCHANA-02"
    )

    slug: str | None = Field(
        default=None,
        sa_column=Column(String(200), nullable=True,index=True)
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



# ORDER


import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from decimal import Decimal
from sqlalchemy import (
    Boolean, Column, String, Integer, ForeignKey,
    Text, Numeric, UniqueConstraint,CheckConstraint
)
from sqlmodel import Field, Relationship
from api.core.base import BaseModel
from sqlalchemy import Column, Text, DateTime

if TYPE_CHECKING:
    from api.models.firm import Firm 
    from api.models.payments import SubscriptionPayment
    from api.models.subscriptions import (
        SubscriptionPlan,
        SubscriptionService,
        SubscriptionTier
        )




class Order(BaseModel, table=True):
    """
    Represents a customer's subscription purchase.

    The order records the selected service, tier, plan, duration,
    and financial details at the time of purchase.

    Typical lifecycle:
        pending_payment -> paid
        pending_payment -> cancelled
        pending_payment -> expired
    """

    __tablename__ = "orders"  # type: ignore

    __table_args__ = (
        UniqueConstraint(
            "order_number",
            name="uq_orders_order_number",
        ),
        CheckConstraint(
            "duration_months > 0",
            name="ck_orders_duration_months_positive",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_orders_amount_positive",
        ),
        CheckConstraint(
            "discount_amount >= 0",
            name="ck_orders_discount_nonnegative",
        ),
        CheckConstraint(
            "total_amount > 0",
            name="ck_orders_total_positive",
        ),
        CheckConstraint(
            "status IN ('pending_payment', 'paid', 'cancelled', 'expired')",
            name="ck_orders_status",
        ),
    )

    # ------------------------------------------------------------------
    # Order identification
    # ------------------------------------------------------------------

    order_number: str = Field(
        sa_column=Column(
            String(64),
            nullable=False,
            unique=True,
            index=True,
        )
    )

    # ------------------------------------------------------------------
    # Foreign keys
    # ------------------------------------------------------------------

    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    service_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_services.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    tier_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_tiers.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    plan_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_plans.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    # ------------------------------------------------------------------
    # Purchase snapshots
    # ------------------------------------------------------------------

    service_name: str = Field(
        sa_column=Column(
            String(120),
            nullable=False,
        )
    )

    tier_name: str = Field(
        sa_column=Column(
            String(120),
            nullable=False,
        )
    )

    plan_name: str = Field(
        sa_column=Column(
            String(120),
            nullable=False,
        )
    )

    duration_months: int = Field(
        gt=0,
        sa_column=Column(
            Integer,
            nullable=False,
        ),
    )

    # ------------------------------------------------------------------
    # Financial details
    # ------------------------------------------------------------------

    amount: Decimal = Field(
        sa_column=Column(
            Numeric(precision=10, scale=2),
            nullable=False,
        )
    )

    discount_amount: Decimal = Field(
        default=Decimal("0.00"),
        sa_column=Column(
            Numeric(precision=10, scale=2),
            nullable=False,
            default=Decimal("0.00"),
            server_default="0.00",
        ),
    )

    total_amount: Decimal = Field(
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

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    status: str = Field(
        default="pending_payment",
        sa_column=Column(
            String(30),
            nullable=False,
            server_default="pending_payment",
            index=True,
        ),
    )

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------

    paid_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True},
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    firm: Optional["Firm"] = Relationship(back_populates="orders")
    service: Optional["SubscriptionService"] = Relationship(back_populates="orders")
    tier: Optional["SubscriptionTier"] = Relationship(back_populates="orders")
    plan: Optional["SubscriptionPlan"] = Relationship(back_populates="orders")
    payments:list["SubscriptionPayment"] = Relationship(back_populates="order")




# PAYMENTS 


"""Receipt model."""
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from decimal import Decimal
from regex import B
from sqlalchemy import (
    Boolean, Column, String, Integer, ForeignKey,
    Text, Numeric, UniqueConstraint,CheckConstraint
)
from sqlmodel import Field, Relationship
from api.core.base import BaseModel
from sqlalchemy import Column, Text, DateTime

if TYPE_CHECKING:
    from api.models.firm import Firm,Branch
    from api.models.orders import Order
    from api.models.receipts import Receipt
    from api.models.subscriptions import SubscriptionPlan







class PaymentMethods(BaseModel, table=True): 
    """
    Payment methods available per country.
    
    Examples:
        Nigeria:  Cash, Paystack, Opay, Flutterwave
        Liberia:  Momo Liberia, Orange Money, Cash
    
    Same name can exist in different countries (e.g. Cash in Nigeria + Liberia).
    Same name CANNOT exist twice in same country.
    """
    __tablename__ = "paymentmethods"  # type: ignore
    

    name: str = Field(
        sa_column=Column(String(100), unique=True,nullable=False, index=True)
        
    )
    
    provider_code: str = Field(
        sa_column=Column(String(100), unique=True,nullable=False, index=True) #new
        
    )
    payment_logo_key: str | None = Field(
    default=None,
    sa_column=Column(
        String(500),
        nullable=True,
    ),
    )
    is_active: bool = Field(
        default=True,
        sa_column=Column(
            Boolean,
            nullable=False,
            server_default="true"
        )
        ) #new

    slug: str | None = Field(
        default=None,
        sa_column=Column(String(150), nullable=True, unique=True, index=True)
    )

    # Relationships
    receipts: list["Receipt"] = Relationship(back_populates="payment_method")
    subscription_payments:list["SubscriptionPayment"]= Relationship(back_populates="payment_method")
    

class SubscriptionPayment(BaseModel, table=True):
    """
    One payment attempt for an order.

    Typical lifecycle:
        pending -> paid
        pending -> failed
        pending -> cancelled

    A payment can remain pending while awaiting gateway confirmation
    or reconciliation after an uncertain gateway response.
    """

    __tablename__ = "subscription_payments"  # type: ignore

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

    # ------------------------------------------------------------------
    # Relationships to existing records
    # ------------------------------------------------------------------

    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    # New: payment belongs to an order.
    order_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    # Retained for convenient access and compatibility with existing logic.
    # The plan should match the plan recorded on the associated order.
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

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    idempotency_key: str = Field(
        sa_column=Column(
            String(100),
            nullable=False,
        )
    )

    # ------------------------------------------------------------------
    # Payment details
    # ------------------------------------------------------------------

    amount: Decimal = Field(
        sa_column=Column(
            Numeric(precision=10, scale=2),
            nullable=False,
        )
    )

    currency: str = Field(
        default="USD",
        sa_column=Column(
            String(3),
            nullable=False,
            server_default="USD",
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

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    status: str = Field(
        default="pending",
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="pending",
        ),
    )

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------

    paid_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True},
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    firm: Optional["Firm"] = Relationship(back_populates="subscription_payments")
    order: Optional["Order"] = Relationship(back_populates="payments")
    plan: Optional["SubscriptionPlan"] = Relationship(back_populates="subscription_payments")
    payment_method: Optional["PaymentMethods"] = Relationship(back_populates="subscription_payments")


# SERVICE




#from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import Column, Text, Boolean, DateTime,String,Integer, ForeignKey,Numeric
from api.core.base import BaseModel, utc_now
from sqlmodel import Field,Relationship,UniqueConstraint,CheckConstraint
from pydantic import  EmailStr
from pydantic_extra_types.phone_numbers import PhoneNumber
from typing import List, Optional
from hashlib import md5

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from api.models.firm import Firm
    from api.models.orders import Order
    from api.models.payments import SubscriptionPayment




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
    
    service_logo_key: str | None = Field(
    default=None,
    sa_column=Column(String(500),nullable=True),
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
    orders: list["Order"] = Relationship(back_populates="service")


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
            "slug",
            name="uq_subscription_tiers_slug"
        ),
         UniqueConstraint(
            "service_id","name",
            name="uq_subscription_tiers_service_name"
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
        sa_column=Column(String(150), nullable=False,index=True)
    )
    description: str = Field(
        sa_column=Column(Text, nullable=False)
    )
   
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true")
    )
    
    is_trial_tier: bool = Field(
        default=False,
        sa_column=Column(Boolean,nullable=False,default=False,index=True,),
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
    
    orders: list["Order"] = Relationship(back_populates="tier")


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
        CheckConstraint(
            "duration_months IN (1, 2, 3, 6, 12)",
            name="ck_subscription_plan_duration_months",
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
        sa_column=Column(String(200), nullable=False, unique=True, index=True)
    )
    description: str = Field(
        sa_column=Column(Text, nullable=False)
    )
    duration_months: int = Field(
        sa_column=Column(Integer, nullable=False),
    )

    price: Decimal = Field(
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
    orders: list["Order"] =  Relationship(back_populates="plan")
    subscription_payments:list["SubscriptionPayment"]= Relationship(back_populates="plan")



class Subscription(BaseModel, table=True):
    """
    Represents a firm's access to a subscription service.

    A subscription can be either a trial or a paid subscription.

    Trial lifecycle:
        is_on_trial = True
        trial_started_at and trial_ends_at are populated.

    Paid subscription lifecycle:
        is_on_trial = False
        subscription_started_at and subscription_ends_at are populated.

    A firm can have only one subscription record per service.
    """

    __tablename__ = "subscriptions"  # type: ignore

    __table_args__ = (
        UniqueConstraint(
            "firm_id",
            "service_id",
            name="unique_subscription_per_firm_service",
        ),
    )

    # ------------------------------------------------------------------
    # Foreign keys
    # ------------------------------------------------------------------

    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    service_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_services.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    tier_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("subscription_tiers.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    plan_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("subscription_plans.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    # ------------------------------------------------------------------
    # Last successful payment
    # ------------------------------------------------------------------

    last_payment_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "subscription_payments.id",
                ondelete="SET NULL",
            ),
            nullable=True,
            index=True,
        ),
    )

    # ------------------------------------------------------------------
    # Trial fields
    # ------------------------------------------------------------------

    is_on_trial: bool = Field(
        default=False,
        index=True,
    )

    trial_started_at: datetime | None = Field(
        default=None,
        nullable=True,
    )

    trial_ends_at: datetime | None = Field(
        default=None,
        nullable=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Subscription status
    # ------------------------------------------------------------------

    is_active: bool = Field(
        default=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Paid subscription dates
    # ------------------------------------------------------------------

    subscription_started_at: datetime | None = Field(
        default=None,
        nullable=True,
    )

    subscription_ends_at: datetime | None = Field(
        default=None,
        nullable=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Renewal settings
    # ------------------------------------------------------------------

    auto_renew: bool = Field(
        default=False,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    firm: Optional["Firm"] = Relationship(
        back_populates="subscriptions",
    )

    service: Optional["SubscriptionService"] = Relationship(
        back_populates="subscriptions",
    )

    tier: Optional["SubscriptionTier"] = Relationship(
        back_populates="subscriptions",
    )

    plan: Optional["SubscriptionPlan"] = Relationship(
        back_populates="subscriptions",
    )

    last_payment: Optional["SubscriptionPayment"] = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Subscription.last_payment_id]",
        },
    )

  
  



#PAYMENT LOGIC

# =============================================================================
# READ — AVAILABLE (public) — active only
# =============================================================================

async def read_available_payment_methods(
    db: AsyncSession,
) -> AvailablePaymentMethodsRead:
    """
    Public 'We accept' widget. Active methods only.

    Inactive methods remain in the DB and are visible to admins.
    """
    try:
        result = await db.execute(
            select(PaymentMethods)
            .where(col(PaymentMethods.is_active).is_(True))
            .order_by(col(PaymentMethods.name))
        )
        methods = list(result.scalars().all())

    except SQLAlchemyError as exc:
        logger.exception("Failed to load available payment methods")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        ) from exc

    return to_payment_method_list(methods)
