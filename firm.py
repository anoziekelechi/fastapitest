async def read_my_firms(
    db: AsyncSession,
    current_user: ReadUser,
    *,
    active_only: bool = False,
) -> FirmListRead:
    """
    List current user's firms with branch_count and firm_logo_url.

    Args:
        active_only:
            When True, return only active firms.
            Defaults to False to preserve existing behaviour.

    No receipt counts — the list contract intentionally omits them.
    """

    guard_user_state(current_user)

    try:
        query = (
            select(Firm)
            .where(Firm.user_id == current_user.id)
            .order_by(col(Firm.name))
        )

        if active_only:
            query = query.where(Firm.is_disabled.is_(False))

        result = await db.execute(query)

        firms = list(result.scalars().all())

        ids = [firm_id_of(f) for f in firms]

        branch_counts, _ = await counts_by_firm(db, ids)

    except HTTPException:
        raise

    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s",
            current_user.id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load your firms. Please try again.",
        )

    if active_only and not firms:
        # Distinguish no firms from firms that are all disabled.
        try:
            result = await db.execute(
                select(Firm.id)
                .where(Firm.user_id == current_user.id)
                .limit(1)
            )

            has_firms = result.scalar_one_or_none() is not None

        except Exception:
            logger.exception(
                "Failed to check firm existence for user_id=%s",
                current_user.id,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to load your firms. Please try again.",
            )

        if not has_firms:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="You don't have any firm associated with you.",
            )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You don't have any active firm associated with you.",
        )

    return to_firm_list(firms, branch_counts)


@router.get(
    "/buy-now/firms",
    response_model=FirmListRead,
    status_code=status.HTTP_200_OK,
)
async def read_buy_now_firms(
    db: AsyncSession = Depends(get_db),
    current_user: ReadUser = Depends(get_current_user),
):
    return await read_my_firms(
        db=db,
        current_user=current_user,
        active_only=True,
    )




# api/models/orders.py

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    String,
    Integer,
    ForeignKey,
    Numeric,
    CheckConstraint,
)
from sqlmodel import Field, Relationship

from api.core.base import BaseModel

if TYPE_CHECKING:
    from api.models.firm import Firm
    from api.models.payments import SubscriptionPayment
    from api.models.subscriptions import (
        OrderItem,
    )


class Order(BaseModel, table=True):
    """
    One checkout order belongs to one user.

    An order can contain multiple subscription items, and each
    item can belong to a different firm.
    """

    __tablename__ = "orders"  # type: ignore

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_checkout', 'pending_payment', "
            "'paid', 'cancelled', 'expired')",
            name="ck_orders_status",
        ),
    )

    order_number: str = Field(
        sa_column=Column(
            String(64),
            nullable=False,
            unique=True,
            index=True,
        )
    )

    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    status: str = Field(
        default="pending_checkout",
        sa_column=Column(
            String(30),
            nullable=False,
            server_default="pending_checkout",
            index=True,
        ),
    )

    paid_at: datetime | None = Field(
        default=None,
        nullable=True,
    )

    items: list["OrderItem"] = Relationship(
        back_populates="order",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
        },
    )

    payments: list["SubscriptionPayment"] = Relationship(
        back_populates="order",
    )



from sqlalchemy import UniqueConstraint


class OrderItem(BaseModel, table=True):
    """
    One subscription purchase inside an order.

    Each item belongs to one firm. An order can contain items
    for multiple firms, including the same service for different firms.
    """

    __tablename__ = "order_items"  # type: ignore

    __table_args__ = (
        CheckConstraint(
            "duration_months IN (1, 2, 3, 6, 12)",
            name="ck_order_items_duration_months",
        ),
        CheckConstraint(
            "unit_price >= 0",
            name="ck_order_items_unit_price",
        ),
        CheckConstraint(
            "discount_amount >= 0",
            name="ck_order_items_discount_amount",
        ),
        CheckConstraint(
            "total_amount >= 0",
            name="ck_order_items_total_amount",
        ),
        CheckConstraint(
            "total_amount = unit_price - discount_amount",
            name="ck_order_items_total_calculation",
        ),
    )

    order_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

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

    # Purchase-time snapshots
    service_name: str = Field(
        sa_column=Column(String(120), nullable=False)
    )

    tier_name: str = Field(
        sa_column=Column(String(120), nullable=False)
    )

    plan_name: str = Field(
        sa_column=Column(String(120), nullable=False)
    )

    duration_months: int = Field(
        sa_column=Column(Integer, nullable=False)
    )

    # Purchase-time financial snapshots
    unit_price: Decimal = Field(
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

    # Relationships
    order: "Order" = Relationship(
        back_populates="items",
    )

    firm: "Firm" = Relationship(
        back_populates="order_items",
    )

    service: "SubscriptionService" = Relationship(
        back_populates="order_items",
    )

    tier: "SubscriptionTier" = Relationship(
        back_populates="order_items",
    )

    plan: "SubscriptionPlan" = Relationship(
        back_populates="order_items",
    )



# firm relationship 
if TYPE_CHECKING:
    from api.models.orders import OrderItem
#remove
orders: list["Order"] = Relationship(back_populates="firm")

subscription_payments: list["SubscriptionPayment"] = Relationship(
    back_populates="firm"
)


#replace
order_items: list["OrderItem"] = Relationship(
    back_populates="firm",
)


#SubscriptionService 

#remove
orders: list["Order"] = Relationship(back_populates="service")
#replace

order_items: list["OrderItem"] = Relationship(
    back_populates="service",
)

#Tier
#remove
orders: list["Order"] = Relationship(back_populates="tier")



#replace
order_items: list["OrderItem"] = Relationship(
    back_populates="tier",
)
#plan
#remove
orders: list["Order"] = Relationship(back_populates="plan")
#replace
order_items: list["OrderItem"] = Relationship(
    back_populates="plan",
)



Remove the Order type-checking imports from your subscription model
and add OrderItem:


if TYPE_CHECKING:
    from api.models.orders import OrderItem
    from api.models.payments import SubscriptionPayment






Small import correction: because OrderItem is defined in api/models/orders.py alongside Order, do not import it from api.models.subscriptions. Your orders.py type-checking imports should instead be:
5. Updated S



if TYPE_CHECKING:
    from api.models.firm import Firm
    from api.models.payments import SubscriptionPayment
    from api.models.subscriptions import (
        SubscriptionPlan,
        SubscriptionService,
        SubscriptionTier,
)





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


# in payments.py
if TYPE_CHECKING:
    from api.models.orders import Order
    from api.models.receipts import Receipt



#payment methods relationships 
subscription_payments: list["SubscriptionPayment"] = Relationship(
    back_populates="payment_method",
)



Remove the old imports for Firm and SubscriptionPlan if they are no longer used elsewhere in that file.




Your SubscriptionPlan should no longer have a subscription_payments relationship, because payments now belong to orders rather than individual plans.



    from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class BuyNowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(gt=0)
    plan_id: int = Field(gt=0)


class BuyNowResponse(BaseModel):
    order_number: str
    order_item_id: int
    firm_id: int
    firm_name: str
    service_name: str
    tier_name: str
    plan_name: str
    duration_months: int
    amount: Decimal
    currency: str


class CheckoutItemRead(BaseModel):
    order_item_id: int
    firm_id: int
    firm_name: str
    service_name: str
    tier_name: str
    plan_name: str
    duration_months: int
    amount: Decimal
    currency: str


class CheckoutDetailsRead(BaseModel):
    order_number: str
    items: list[CheckoutItemRead]
    total_amount: Decimal
    currency: str

    # Reuse the existing public response schema.
    payment_methods: AvailablePaymentMethodsRead




import secrets
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession


UNFINISHED_ORDER_STATUSES = (
    "pending_checkout",
    "pending_payment",
)


def generate_order_number() -> str:
    return f"ORD-{secrets.token_hex(8).upper()}"


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

        firm_result = await db.execute(
            select(Firm)
            .where(
                Firm.id == data.firm_id,
                Firm.user_id == current_user.id,
            )
            .with_for_update()
        )
        firm = firm_result.scalar_one_or_none()

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




from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession


async def get_checkout_details(
    order_number: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> CheckoutDetailsRead:
    guard_user_state(current_user)

    try:
        result = await db.execute(
            select(Order)
            .options(
                selectinload(Order.items).selectinload(OrderItem.firm),
            )
            .where(
                Order.order_number == order_number,
                Order.user_id == current_user.id,
                Order.status.in_(UNFINISHED_ORDER_STATUSES),
            )
        )

        order = result.scalar_one_or_none()

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Checkout order not found.",
            )

        if not order.items:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Your checkout order is empty.",
            )

        currencies = {item.currency for item in order.items}

        if len(currencies) != 1:
            logger.error(
                "Order %s contains inconsistent currencies: %s",
                order.order_number,
                currencies,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This order contains inconsistent currencies.",
            )

        currency = next(iter(currencies))
        total_amount = Decimal("0.00")
        checkout_items: list[CheckoutItemRead] = []

        for item in order.items:
            if item.firm is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A firm associated with this order could not be found.",
                )

            item_amount = Decimal(str(item.total_amount))
            total_amount += item_amount

            checkout_items.append(
                CheckoutItemRead(
                    order_item_id=item.id,
                    firm_id=item.firm_id,
                    firm_name=item.firm.name,
                    service_name=item.service_name,
                    tier_name=item.tier_name,
                    plan_name=item.plan_name,
                    duration_months=item.duration_months,
                    amount=item_amount,
                    currency=item.currency,
                )
            )

        # Reuse your existing public active payment-method function.
        payment_methods = await read_available_payment_methods(db)

        return CheckoutDetailsRead(
            order_number=order.order_number,
            items=checkout_items,
            total_amount=total_amount,
            currency=currency,
            payment_methods=payment_methods,
        )

    except HTTPException:
        raise

    except SQLAlchemyError:
        logger.exception(
            "Failed to load checkout for user_id=%s, order_number=%s",
            current_user.id,
            order_number,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to load checkout details. Please try again.",
        )






@router.post(
    "/buy-now",
    response_model=BuyNowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def buy_now(
    data: BuyNowRequest,
    db: AsyncSession = Depends(get_db),
    current_user: ReadUser = Depends(get_current_user),
):
    return await buy_subscription_now(
        data=data,
        db=db,
        current_user=current_user,
    )


@router.get(
    "/checkout/{order_number}",
    response_model=CheckoutDetailsRead,
)
async def checkout_details(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    current_user: ReadUser = Depends(get_current_user),
):
    return await get_checkout_details(
        order_number=order_number,
        db=db,
        current_user=current_user,
    )















