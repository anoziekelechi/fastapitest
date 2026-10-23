# api/subscriptions/routes.py

from fastapi import (
    APIRouter,
    File,
    Form,
    Response,
    UploadFile,
    status,
)

from api.core.database import DBDep
from api.subscriptions.logics import (
    create_plan,
    create_service,
    create_tier,
    delete_plan,
    delete_service,
    delete_tier,
    read_plan_by_id,
    read_plans_for_tier,
    read_service_by_slug,
    read_service_with_tiers,
    read_services,
    read_tier_by_id,
    read_tiers_for_service,
    update_plan,
    update_service,
    update_tier,
)
from api.subscriptions.schemas import (
    CreatePlanResponse,
    CreateServiceResponse,
    CreateTierResponse,
    PlanCreate,
    PlanRead,
    PlanUpdate,
    ServiceCreate,
    ServiceRead,
    ServiceUpdate,
    ServiceWithTiersRead,
    TierCreate,
    TierRead,
    TierUpdate,
    UpdatePlanResponse,
    UpdateServiceResponse,
    UpdateTierResponse,
)
from api.users.deps import AdminUser, OptionalCurrentUser


router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])


# =============================================================================
# ORDERING RULE
#
# Literal paths must come BEFORE paths with a dynamic segment at the
# same depth. E.g.:
#
#   /services                   ← literal
#   /services/{slug}            ← dynamic
#   /services/{slug}/pricing    ← dynamic + literal suffix (safe after)
#   /services/{slug}/tiers      ← dynamic + literal suffix (safe after)
#
# FastAPI matches top-to-bottom, so /services/{slug} would swallow
# /services/anything if declared first. This file follows that order.
# =============================================================================


# =============================================================================
# SERVICE — public reads
# =============================================================================

@router.get(
    "/services",
    response_model=list[ServiceRead],
    status_code=status.HTTP_200_OK,
    summary="List services (public: buyable only; admin: all)",
)
async def list_services(
    db: DBDep,
    current_user: OptionalCurrentUser,
    skip: int = 0,
    limit: int = 50,
):
    """
    Public callers see only services with at least one active tier
    and one active plan. Admins see every service.
    """
    return await read_services(
        db=db,
        current_user=current_user,
        skip=skip,
        limit=limit,
    )


# =============================================================================
# SERVICE — public detail (before /{slug} to keep route ordering explicit)
#
# This block uses literal suffixes on the {slug} segment, which are
# safe to declare before the bare /{slug} route.
# =============================================================================

@router.get(
    "/services/{slug}/pricing",
    response_model=ServiceWithTiersRead,
    status_code=status.HTTP_200_OK,
    summary="Service with tiers and plans (public: active only)",
)
async def service_pricing(
    slug: str,
    db: DBDep,
    current_user: OptionalCurrentUser,
):
    return await read_service_with_tiers(
        slug=slug, db=db, current_user=current_user
    )


@router.get(
    "/services/{slug}/tiers",
    response_model=list[TierRead],
    status_code=status.HTTP_200_OK,
    summary="Tiers for a service (public: active only)",
)
async def list_tiers_for_service(
    slug: str,
    db: DBDep,
    current_user: OptionalCurrentUser,
):
    return await read_tiers_for_service(
        slug=slug, db=db, current_user=current_user
    )


@router.get(
    "/services/{slug}",
    response_model=ServiceRead,
    status_code=status.HTTP_200_OK,
    summary="Service detail (public: active + buyable only)",
)
async def get_service(
    slug: str,
    db: DBDep,
    current_user: OptionalCurrentUser,
):
    return await read_service_by_slug(
        slug=slug, db=db, current_user=current_user
    )


# =============================================================================
# SERVICE — admin writes (multipart)
# =============================================================================

@router.post(
    "/services",
    response_model=CreateServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a subscription service (admin only)",
)
async def create_service_route(
    db: DBDep,
    admin: AdminUser,
    name: str = Form(...),
    description: str = Form(...),
    trial_days: int = Form(30),
    service_image: UploadFile | None = File(None),
):
    """
    Multipart endpoint.

    FastAPI cannot read a Pydantic body and a file from the same
    request. So each field is declared individually and re-assembled
    into `ServiceCreate` before hitting the logic — that way the
    schema validators still run.
    """
    data = ServiceCreate(
        name=name,
        description=description,
        trial_days=trial_days,
    )

    return await create_service(
        data=data,
        service_image=service_image,
        db=db,
        current_user=admin,
    )


@router.patch(
    "/services/{slug}",
    response_model=UpdateServiceResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a subscription service (admin only)",
)
async def patch_service(
    slug: str,
    db: DBDep,
    admin: AdminUser,
    description: str | None = Form(None),
    trial_days: int | None = Form(None),
    is_active: bool | None = Form(None),
    service_image: UploadFile | None = File(None),
):
    data = ServiceUpdate(
        description=description,
        trial_days=trial_days,
        is_active=is_active,
    )

    return await update_service(
        slug=slug,
        data=data,
        db=db,
        service_image=service_image,
    )


@router.delete(
    "/services/{slug}",
    response_model=Response,
    status_code=status.HTTP_200_OK,
    summary="Delete a subscription service (admin only)",
)
async def remove_service(
    slug: str,
    db: DBDep,
    admin: AdminUser,
):
    # The service returns a dict; wrap it so response_model=Response
    # works or drop the response_model and let FastAPI infer from dict.
    # Simplest: use response_model=dict / no model.
    return await delete_service(
        slug=slug, db=db, current_user=admin
    )


# =============================================================================
# TIER — public reads
# =============================================================================

@router.get(
    "/tiers/{tier_id}/plans",
    response_model=list[PlanRead],
    status_code=status.HTTP_200_OK,
    summary="Plans for a tier (public: active only)",
)
async def list_plans_for_tier(
    tier_id: int,
    db: DBDep,
    current_user: OptionalCurrentUser,
):
    return await read_plans_for_tier(
        tier_id=tier_id, db=db, current_user=current_user
    )


@router.get(
    "/tiers/{tier_id}",
    response_model=TierRead,
    status_code=status.HTTP_200_OK,
    summary="Tier detail (public: active + reachable only)",
)
async def get_tier(
    tier_id: int,
    db: DBDep,
    current_user: OptionalCurrentUser,
):
    return await read_tier_by_id(
        tier_id=tier_id, db=db, current_user=current_user
    )


# =============================================================================
# TIER — admin writes
# =============================================================================

@router.post(
    "/tiers",
    response_model=CreateTierResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a subscription tier (admin only)",
)
async def create_tier_route(
    data: TierCreate,
    db: DBDep,
    admin: AdminUser,
):
    # No file here, so a JSON body is fine
    return await create_tier(
        data=data, db=db, current_user=admin
    )


@router.patch(
    "/tiers/{tier_id}",
    response_model=UpdateTierResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a subscription tier (admin only)",
)
async def patch_tier(
    tier_id: int,
    data: TierUpdate,
    db: DBDep,
    admin: AdminUser,
):
    return await update_tier(
        tier_id=tier_id, data=data, db=db, current_user=admin
    )


@router.delete(
    "/tiers/{tier_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a subscription tier (admin only)",
)
async def remove_tier(
    tier_id: int,
    db: DBDep,
    admin: AdminUser,
):
    return await delete_tier(
        tier_id=tier_id, db=db, current_user=admin
    )


# =============================================================================
# PLAN — public reads
# =============================================================================

@router.get(
    "/plans/{plan_id}",
    response_model=PlanRead,
    status_code=status.HTTP_200_OK,
    summary="Plan detail (public: active + reachable only)",
)
async def get_plan(
    plan_id: int,
    db: DBDep,
    current_user: OptionalCurrentUser,
):
    return await read_plan_by_id(
        plan_id=plan_id, db=db, current_user=current_user
    )


# =============================================================================
# PLAN — admin writes
# =============================================================================

@router.post(
    "/plans",
    response_model=CreatePlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a subscription plan (admin only)",
)
async def create_plan_route(
    data: PlanCreate,
    db: DBDep,
    admin: AdminUser,
):
    return await create_plan(
        data=data, db=db, current_user=admin
    )


@router.patch(
    "/plans/{plan_id}",
    response_model=UpdatePlanResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a subscription plan (admin only)",
)
async def patch_plan(
    plan_id: int,
    data: PlanUpdate,
    db: DBDep,
    admin: AdminUser,
):
    return await update_plan(
        plan_id=plan_id, data=data, db=db, current_user=admin
    )


@router.delete(
    "/plans/{plan_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a subscription plan (admin only)",
)
async def remove_plan(
    plan_id: int,
    db: DBDep,
    admin: AdminUser,
):
    return await delete_plan(
        plan_id=plan_id, db=db, current_user=admin
    )








# =============================================================================
# CHECKOUT (Step 2)
# =============================================================================

# api/subscriptions/logics.py (additions)

from decimal import Decimal

from api.core.guards import guard_firm_state, guard_user_state
from api.payments.gateways import initiate_payment, verify_payment
from api.models.subscriptions import SubscriptionPayment


# =============================================================================
# CHECKOUT — Step 2
# =============================================================================

async def get_checkout_details(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Load everything needed to render the checkout page.

    Validates:
        - user is enabled + verified
        - user owns at least one active firm
        - plan and its full tier → service chain exist and are active
    """

    # ------------------------------------------------------------------
    # 1. User state
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Pick the user's active firm
    #
    # If they own multiple, prefer the first non-disabled one. If they
    # own none (or all disabled), they need to create a firm first.
    # ------------------------------------------------------------------
    from api.models.users import Firm

    try:
        firms_result = await db.execute(
            select(Firm)
            .where(col(Firm.user_id) == current_user.id)
            .order_by(col(Firm.created_at))
        )
        firms = firms_result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s", current_user.id
        )
        raise HTTPException(500, "Failed to load firm. Please try again.")

    active_firm = next((f for f in firms if not f.disabled), None)

    if active_firm is None:
        if not firms:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You need to create a firm before subscribing.",
                headers={"X-Error-Code": "no_firm"},
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="All your firms are disabled. Please contact admin.",
            headers={"X-Error-Code": "firm_suspended"},
        )

    # ------------------------------------------------------------------
    # 3. Load plan + tier + service
    # ------------------------------------------------------------------
    plan = await get_plan_by_id(db, plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(404, f"Plan id={plan_id} not found or inactive")

    tier = await get_tier_by_id(db, plan.tier_id)
    if not tier or not tier.is_active:
        raise HTTPException(
            404, f"Tier id={plan.tier_id} not found or inactive"
        )

    service = await db.get(SubscriptionService, tier.service_id)
    if not service or not service.is_active:
        raise HTTPException(
            404, f"Service id={tier.service_id} not found or inactive"
        )

    # ------------------------------------------------------------------
    # 4. Payment methods available
    # ------------------------------------------------------------------
    from api.models.home import PaymentMethods

    try:
        pm_result = await db.execute(
            select(PaymentMethods).order_by(col(PaymentMethods.name))
        )
        payment_methods = pm_result.scalars().all()
    except Exception:
        logger.exception("Failed to load payment methods")
        raise HTTPException(
            500, "Failed to load payment methods. Please try again."
        )

    from api.payments.logics import to_payment_method_read
    from api.firms.schemas import FirmRead
    from api.subscriptions.schemas import (
        CheckoutResponse,
        PlanRead,
        ServiceRead,
        TierRead,
    )

    return CheckoutResponse(
        plan=to_plan_read(plan),
        tier=TierRead.model_validate(tier),
        service=ServiceRead.model_validate(service),
        firm=FirmRead.model_validate(active_firm),
        payment_methods=[
            to_payment_method_read(pm) for pm in payment_methods
        ],
        amount=plan.price,
        currency=settings.currency_code,
    )


# =============================================================================
# PURCHASE — Step 4
# =============================================================================

async def initiate_subscription_purchase(
    data: SubscriptionPurchase,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Start a payment. Does NOT activate the subscription yet.

    Validates:
        - user is enabled + verified
        - user has an active firm
        - plan → tier → service chain is valid
        - payment method exists
        - no pending payment for the same firm+plan already in flight

    Creates a SubscriptionPayment record with status="pending",
    calls the gateway, and returns the reference.
    """

    # ------------------------------------------------------------------
    # 1. User state
    # ------------------------------------------------------------------
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Active firm
    # ------------------------------------------------------------------
    from api.models.users import Firm

    try:
        firms_result = await db.execute(
            select(Firm)
            .where(col(Firm.user_id) == current_user.id)
            .order_by(col(Firm.created_at))
        )
        firms = firms_result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s", current_user.id
        )
        raise HTTPException(500, "Failed to load firm. Please try again.")

    active_firm = next((f for f in firms if not f.disabled), None)

    if active_firm is None:
        if not firms:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You need to create a firm before subscribing.",
                headers={"X-Error-Code": "no_firm"},
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="All your firms are disabled. Please contact admin.",
            headers={"X-Error-Code": "firm_suspended"},
        )

    # Firm is guaranteed enabled (we filtered). Guard anyway:
    guard_firm_state(active_firm)

    # ------------------------------------------------------------------
    # 3. Plan → tier → service chain
    # ------------------------------------------------------------------
    plan = await get_plan_by_id(db, data.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(404, f"Plan id={data.plan_id} not found or inactive")

    tier = await get_tier_by_id(db, plan.tier_id)
    if not tier or not tier.is_active:
        raise HTTPException(
            404, f"Tier id={plan.tier_id} not found or inactive"
        )

    service = await db.get(SubscriptionService, tier.service_id)
    if not service or not service.is_active:
        raise HTTPException(
            404, f"Service id={tier.service_id} not found or inactive"
        )

    # ------------------------------------------------------------------
    # 4. Payment method
    # ------------------------------------------------------------------
    from api.models.home import PaymentMethods

    payment_method = await db.get(PaymentMethods, data.payment_method_id)
    if not payment_method:
        raise HTTPException(
            404, f"Payment method id={data.payment_method_id} not found"
        )

    # ------------------------------------------------------------------
    # 5. Reject duplicate pending payments
    #
    # If a user double-clicks Buy Now, don't create two pending payments.
    # ------------------------------------------------------------------
    try:
        existing_pending = (
            await db.execute(
                select(SubscriptionPayment).where(
                    col(SubscriptionPayment.firm_id) == active_firm.id,
                    col(SubscriptionPayment.plan_id) == data.plan_id,
                    col(SubscriptionPayment.status) == "pending",
                )
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check pending payments firm_id=%s", active_firm.id
        )
        raise HTTPException(
            500, "Failed to start purchase. Please try again."
        )

    if existing_pending:
        raise HTTPException(
            409,
            "A payment for this plan is already pending. "
            "Please complete or wait for it to expire.",
            headers={"X-Error-Code": "payment_pending"},
        )

    # ------------------------------------------------------------------
    # 6. Create the pending payment record
    # ------------------------------------------------------------------
    reference = f"sub_{secrets.token_urlsafe(16)}"

    payment = SubscriptionPayment(
        firm_id=active_firm.id,
        plan_id=data.plan_id,
        payment_method_id=data.payment_method_id,
        amount=plan.price,
        currency=settings.currency_code,
        payment_reference=reference,
        status="pending",
    )

    try:
        db.add(payment)
        await db.commit()
        await db.refresh(payment)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create payment record firm_id=%s plan_id=%s",
            active_firm.id,
            data.plan_id,
        )
        raise HTTPException(
            500, "Failed to start purchase. Please try again."
        )

    # ------------------------------------------------------------------
    # 7. Call the gateway
    # ------------------------------------------------------------------
    provider = payment_method.name.lower().replace(" ", "_")

    try:
        gateway_response = await initiate_payment(
            provider=provider,
            amount=str(plan.price),
            currency=settings.currency_code,
            reference=reference,
            customer_phone=current_user.phone if hasattr(current_user, "phone") else None,
        )
    except Exception:
        logger.exception(
            "Gateway initiation failed for payment ref=%s", reference
        )

        # Mark the payment as failed so it doesn't block new attempts
        payment.status = "failed"
        db.add(payment)
        try:
            await db.commit()
        except Exception:
            await db.rollback()

        raise HTTPException(
            500,
            "Payment gateway is unavailable. Please try again.",
        )

    logger.info(
        "Purchase initiated: firm_id=%s plan_id=%s ref=%s provider=%s",
        active_firm.id,
        data.plan_id,
        reference,
        provider,
    )

    return {
        "message": "Payment initiated. Complete the payment to activate.",
        "payment_reference": reference,
        "amount": plan.price,
        "currency": settings.currency_code,
        "status": gateway_response.get("status", "pending"),
        "provider_url": gateway_response.get("provider_url"),
    }


# =============================================================================
# CONFIRM — Step 5
# =============================================================================







# =============================================================================
# CHECKOUT — Step 2
# =============================================================================

@router.get(
    "/checkout/{plan_id}",
    response_model=CheckoutResponse,
    status_code=status.HTTP_200_OK,
    summary="All data needed for the checkout page",
)
async def checkout_details(
    plan_id: int,
    db: DBDep,
    current_user: CurrentUser,
):
    return await get_checkout_details(
        plan_id=plan_id, db=db, current_user=current_user
    )







# api/subscriptions/logics.py
# (only the read section changes — everything else identical)
# Inside confirm_subscription_payment, after creating/updating `sub`:

# ... existing sub creation / update logic ...

# Attach the payment that activated / extended this subscription
sub.last_payment_id = payment.id

db.add(sub)
db.add(payment)
















