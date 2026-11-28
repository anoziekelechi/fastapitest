async def read_available_trial_services(
    firm_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> TrialServiceListRead:
    """
    List services the firm can still start a trial for.

    A service is available when ALL of the following hold:
        - The service is active.
        - The firm has never had a subscription for it
          (trial or paid).
        - The service has at least one ACTIVE plan
          (all services are trialable now — no tier restriction).
    """
    guard_user_state(current_user)

    await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=False,
    )

    # ------------------------------------------------------------------
    # 1. Service IDs the firm already has a subscription for
    # ------------------------------------------------------------------
    try:
        used_rows = await db.execute(
            select(Subscription.service_id)
            .where(col(Subscription.firm_id) == firm_id)
        )
        used_service_ids = {row[0] for row in used_rows.all()}
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load used services for firm_id=%s", firm_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load trial services. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 2. Active services with at least one active plan
    # ------------------------------------------------------------------
    buyable_plan_exists = (
        select(SubscriptionPlan.id)
        .where(
            col(SubscriptionPlan.service_id)
            == col(SubscriptionService.id),
            col(SubscriptionPlan.is_active).is_(True),
        )
        .correlate(SubscriptionService)
        .exists()
    )

    try:
        result = await db.execute(
            select(SubscriptionService)
            .where(
                col(SubscriptionService.is_active).is_(True),
                buyable_plan_exists,
            )
            .order_by(col(SubscriptionService.name).asc())
        )
        services = list(result.scalars().all())
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load services for trial listing: firm_id=%s",
            firm_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load trial services. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 3. Filter out already-used services
    # ------------------------------------------------------------------
    available = [
        s for s in services
        if service_id_of(s) not in used_service_ids
    ]

    # ------------------------------------------------------------------
    # 4. Cheapest plan per service (for "from $X")
    # ------------------------------------------------------------------
    service_ids = [service_id_of(s) for s in available]
    cheapest: dict[int, object] = {}

    if service_ids:
        try:
            rows = await db.execute(
                select(
                    col(SubscriptionPlan.service_id),
                    func.min(SubscriptionPlan.price),
                )
                .where(
                    col(SubscriptionPlan.service_id).in_(service_ids),
                    col(SubscriptionPlan.is_active).is_(True),
                )
                .group_by(col(SubscriptionPlan.service_id))
            )
            cheapest = {row[0]: row[1] for row in rows.all()}
        except SQLAlchemyError:
            logger.exception(
                "Failed to compute cheapest plans: firm_id=%s",
                firm_id,
            )
            # Non-fatal — proceed without price_from

    # ------------------------------------------------------------------
    # 5. Assemble
    # ------------------------------------------------------------------
    items = [
        TrialServiceRead(
            service_id=service_id_of(s),
            service_name=s.name,
            service_slug=s.slug or s.name,
            service_logo_url=resolve_logo_url(s.service_logo_key),
            description=s.description,
            trial_days=s.trial_days,
            price_from=cheapest.get(service_id_of(s)),
        )
        for s in available
    ]

    return TrialServiceListRead(
        firm_id=firm_id,
        total=len(items),
        services=items,
    )


async def start_trial_for_service(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Start a free trial for a firm on a specific service.

    Preconditions:
        - Firm is accessible and active.
        - Service exists and is active.
        - Firm has NEVER had a subscription for this service
          (trial or paid).
        - Service has at least one ACTIVE plan.

    The trial is created without a plan — the firm chooses a plan
    when they upgrade. Trials are free by definition, so no tier
    or plan is bound until payment.
    """
    guard_user_state(current_user)

    firm = await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=True,
    )
    fid = firm_id_of(firm)

    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    try:
        service = await db.get(SubscriptionService, service_id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found or inactive.",
        )

    # ------------------------------------------------------------------
    # 2. Firm must not have tried this service before
    # ------------------------------------------------------------------
    existing = await get_subscription_by_firm_service(
        db, fid, service_id,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This firm has already used '{service.name}'. "
                f"Free trials are only available once per service."
            ),
        )

    # ------------------------------------------------------------------
    # 3. Service must be buyable (has at least one active plan)
    # ------------------------------------------------------------------
    if not await service_has_buyable(db, service_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{service.name}' is not currently available for "
                f"trial — no active plan."
            ),
        )

    # ------------------------------------------------------------------
    # 4. Create the trial subscription
    # ------------------------------------------------------------------
    now = utc_now()
    trial_ends_at = now + timedelta(days=service.trial_days)

    sub = Subscription(
        firm_id=fid,
        service_id=service_id,
        plan_id=None,
        last_payment_id=None,
        is_on_trial=True,
        trial_started_at=now,
        trial_ends_at=trial_ends_at,
        is_active=True,
        subscription_started_at=None,
        subscription_ends_at=None,
        auto_renew=False,
    )
    db.add(sub)

    try:
        await db.flush()
    except IntegrityError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after trial IntegrityError: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.warning(
            "Trial creation violated a database integrity constraint: "
            "firm_id=%s service_id=%s",
            fid, service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This firm already has a subscription for "
                f"'{service.name}'."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after trial flush error: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.exception(
            "Database error while flushing trial: "
            "firm_id=%s service_id=%s", fid, service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to start the trial. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after unexpected trial flush error: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.exception(
            "Unexpected error while flushing trial: "
            "firm_id=%s service_id=%s", fid, service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while starting trial.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "firm_id=%s service_id=%s", fid, service_id,
            )
        logger.exception(
            "Trial commit failed or has an uncertain outcome: "
            "firm_id=%s service_id=%s", fid, service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The trial could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(sub)
    except Exception:
        logger.exception(
            "Failed to refresh subscription after trial: "
            "firm_id=%s service_id=%s", fid, service_id,
        )

    logger.info(
        "Trial started: firm_id=%s service_id=%s trial_ends_at=%s "
        "by user_id=%s",
        fid, service_id, trial_ends_at, current_user.id,
    )

    return {
        "message": (
            f"Free trial for '{service.name}' activated until "
            f"{trial_ends_at.strftime('%Y-%m-%d')}."
        ),
        "subscription": to_subscription_read(sub, now=now),
    }


async def read_subscription_status(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> SubscriptionStatusRead:
    """
    Compact status for the 'is my firm subscribed?' widget.

    - Includes plan name (if a plan is bound).
    - No tier_name — plans are the smallest granularity now.
    """
    guard_user_state(current_user)

    await require_firm_access(
        db,
        firm_id=firm_id,
        current_user=current_user,
        require_active=False,
    )

    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    try:
        service = await db.get(SubscriptionService, service_id)
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load service id=%s", service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    # ------------------------------------------------------------------
    # 2. Load the subscription
    # ------------------------------------------------------------------
    sub = await get_subscription_by_firm_service(
        db, firm_id, service_id,
    )
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found for this firm and service.",
        )

    # ------------------------------------------------------------------
    # 3. Resolve the plan name (if any)
    # ------------------------------------------------------------------
    plan = (
        await db.get(SubscriptionPlan, sub.plan_id)
        if sub.plan_id is not None
        else None
    )

    # ------------------------------------------------------------------
    # 4. Compute status + assemble
    # ------------------------------------------------------------------
    now = utc_now()
    status_label, can_access = compute_status_label(
        is_on_trial=sub.is_on_trial,
        is_active=sub.is_active,
        trial_ends_at=sub.trial_ends_at,
        subscription_ends_at=sub.subscription_ends_at,
        now=now,
    )
    read = to_subscription_read(sub, now=now)

    return SubscriptionStatusRead(
        firm_id=firm_id,
        service_id=service_id,
        service_name=service.name,
        plan_name=plan.name if plan else None,
        is_active=read.is_active,
        is_on_trial=read.is_on_trial,
        days_remaining=read.days_remaining or 0,
        status_label=read.status_label,
        can_access=can_access,
    )


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
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch subscription firm_id=%s service_id=%s",
            firm_id, service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load subscription. Please try again.",
        ) from exc


async def activate_subscription(
    data: SubscriptionActivate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Admin override: activate or extend a subscription without payment.

    Body: `{ firm_id, service_id, plan_slug }`.

    Plan resolution:
        - Plan is looked up by `(service_id, plan_slug)` — scoped to
          the service in the request. Wrong service → 404.
        - Plan must be active.

    Extension semantics:
        - Same plan, not on trial, still active, end date in the
          future → extend from current end date.
        - Otherwise → start a fresh window from now.
    """
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )

    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 1. Resolve firm + service
    # ------------------------------------------------------------------
    try:
        firm = await db.get(Firm, data.firm_id)
        service = await db.get(SubscriptionService, data.service_id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load activation resources.",
        ) from exc

    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firm not found.",
        )
    fid = firm_id_of(firm)
    guard_firm_state(firm)

    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found or inactive.",
        )
    sid = service_id_of(service)

    # ------------------------------------------------------------------
    # 2. Resolve + validate plan (scoped to service)
    # ------------------------------------------------------------------
    plan = await get_plan_by_service_and_slug(
        db, service_id=sid, plan_slug=data.plan_slug,
    )
    pid = plan_id_of(plan)

    if not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected plan is inactive.",
        )

    # ------------------------------------------------------------------
    # 3. Load existing sub + determine extension base
    # ------------------------------------------------------------------
    now = utc_now()
    sub = await get_subscription_by_firm_service(db, fid, sid)

    same_plan = (
        sub is not None
        and sub.plan_id == pid
        and not sub.is_on_trial
        and sub.is_active
        and sub.subscription_ends_at is not None
        and as_utc(sub.subscription_ends_at) > now
    )

    if same_plan:
        assert sub is not None
        assert sub.subscription_ends_at is not None
        base_time = as_utc(sub.subscription_ends_at)
    else:
        base_time = now

    ends_at = add_calendar_months(base_time, plan.duration_months)

    # ------------------------------------------------------------------
    # 4. Create or update
    # ------------------------------------------------------------------
    if sub is None:
        sub = Subscription(
            firm_id=fid,
            service_id=sid,
            plan_id=pid,
            last_payment_id=None,
            is_on_trial=False,
            trial_started_at=None,
            trial_ends_at=None,
            is_active=True,
            subscription_started_at=now,
            subscription_ends_at=ends_at,
            auto_renew=False,
        )
        db.add(sub)
    else:
        sub.plan_id = pid
        sub.is_on_trial = False
        sub.trial_started_at = None
        sub.trial_ends_at = None
        sub.is_active = True
        sub.subscription_ends_at = ends_at
        sub.auto_renew = False
        if not same_plan:
            sub.subscription_started_at = now
        db.add(sub)

    # ------------------------------------------------------------------
    # 5. Flush → commit → refresh
    # ------------------------------------------------------------------
    try:
        await db.flush()
    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after activation flush error: "
                "firm_id=%s service_id=%s", fid, sid,
            )
        logger.exception(
            "Database error while flushing activation: "
            "firm_id=%s service_id=%s", fid, sid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to activate subscription. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after unexpected activation flush error: "
                "firm_id=%s service_id=%s", fid, sid,
            )
        logger.exception(
            "Unexpected error while flushing activation: "
            "firm_id=%s service_id=%s", fid, sid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while activating subscription.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "firm_id=%s service_id=%s", fid, sid,
            )
        logger.exception(
            "Activation commit failed or has an uncertain outcome: "
            "firm_id=%s service_id=%s", fid, sid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The activation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(sub)
    except Exception:
        logger.exception(
            "Failed to refresh subscription after activation: "
            "firm_id=%s service_id=%s", fid, sid,
        )

    logger.info(
        "Admin activated subscription: firm_id=%s service_id=%s "
        "plan_id=%s plan_slug=%s by user_id=%s",
        fid, sid, pid, plan.slug, current_user.id,
    )

    return {
        "message": (
            f"Subscription activated for '{service.name}' until "
            f"{ends_at.strftime('%Y-%m-%d')}."
        ),
        "subscription": to_subscription_read(sub, now=now),
    }







# is this not the same with read_plans_ffor_service
async def read_service_with_plans(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceWithPlansRead:
    """
    Read a service with plans.

    Admin: all plans. Public: active plans only, service must be
    active and buyable.
    """
    admin = bool(current_user and getattr(current_user, "is_admin", False))
    service = await get_service_by_slug(db, slug)
    return await load_service_detail(db, service, admin=admin)


async def read_plans_for_service(
    service_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> list[PlanRead]:
    """
    List all plans under a service (admin-only).

    URL: GET /services/{service_slug}/plans

    Ordered by duration_months asc, then name asc.
    """
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    try:
        result = await db.execute(
            select(SubscriptionPlan)
            .where(col(SubscriptionPlan.service_id) == sid)
            .order_by(
                col(SubscriptionPlan.duration_months).asc(),
                col(SubscriptionPlan.name).asc(),
            )
        )
        plans = list(result.scalars().all())
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load plans for service_id=%s", sid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plans. Please try again.",
        ) from exc

    return to_plan_list(plans)




@router.post(
    "/services",
    response_model=CreateServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a subscription service (admin only)",
)
async def create_service_route(
    db: DBDep,
    admin: AdminUser,
    data: Annotated[ServiceCreate, Form()],
    service_image: Annotated[UploadFile | None, File()] = None,
):
    return await create_service(
        data=data,
        service_image=service_image,
        db=db,
        current_user=admin,
    )

vs

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







# api/subscriptions/logics/subscription_ops.py

async def start_trial_for_service(
    firm_id: int,
    service_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Start a free trial for one service."""
    guard_user_state(current_user)

    firm = await require_firm_access(
        db, firm_id=firm_id, current_user=current_user,
        require_active=True,
    )
    fid = firm_id_of(firm)

    try:
        service = await db.get(SubscriptionService, service_id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found or inactive.",
        )

    existing = await get_subscription_by_firm_service(db, fid, service_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This firm has already used '{service.name}'. "
                f"Free trials are only available once per service."
            ),
        )

    # Service must have at least one active plan
    if not await service_has_buyable(db, service_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{service.name}' is not currently available "
                f"for trial — no active plan."
            ),
        )

    now = utc_now()
    trial_ends_at = now + timedelta(days=service.trial_days)

    sub = Subscription(
        firm_id=fid,
        service_id=service_id,
        plan_id=None,             # trial doesn't bind a plan
        is_on_trial=True,
        trial_started_at=now,
        trial_ends_at=trial_ends_at,
        is_active=True,
        subscription_started_at=None,
        subscription_ends_at=None,
        auto_renew=False,
    )
    db.add(sub)

    # ... flush → commit → refresh as before





async def activate_subscription(
    data: SubscriptionActivate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Admin: activate / extend a subscription without payment."""
    # data now has: firm_id, service_id, plan_id
    if not is_admin(current_user):
        raise HTTPException(403, "Administrator access required.")

    guard_user_state(current_user)

    firm = await db.get(Firm, data.firm_id)
    service = await db.get(SubscriptionService, data.service_id)
    if firm is None:
        raise HTTPException(404, "Firm not found.")
    fid = firm_id_of(firm)
    guard_firm_state(firm)

    if service is None or not service.is_active:
        raise HTTPException(404, "Subscription service not found or inactive.")
    sid = service_id_of(service)

    plan = await get_plan_by_service_and_slug(
        db, service_id=sid, plan_slug=data.plan_slug,
    )
    pid = plan_id_of(plan)
    if not plan.is_active:
        raise HTTPException(400, "The selected plan is inactive.")

    now = utc_now()
    sub = await get_subscription_by_firm_service(db, fid, sid)

    same_plan = (
        sub is not None
        and sub.plan_id == pid
        and not sub.is_on_trial
        and sub.is_active
        and sub.subscription_ends_at is not None
        and as_utc(sub.subscription_ends_at) > now
    )

    base_time = (
        as_utc(sub.subscription_ends_at)
        if same_plan and sub and sub.subscription_ends_at
        else now
    )
    ends_at = add_calendar_months(base_time, plan.duration_months)

    if sub is None:
        sub = Subscription(
            firm_id=fid,
            service_id=sid,
            plan_id=pid,
            is_on_trial=False,
            trial_started_at=None,
            trial_ends_at=None,
            is_active=True,
            subscription_started_at=now,
            subscription_ends_at=ends_at,
            auto_renew=False,
        )
        db.add(sub)
    else:
        sub.plan_id = pid
        sub.is_on_trial = False
        sub.trial_started_at = None
        sub.trial_ends_at = None
        sub.is_active = True
        sub.subscription_ends_at = ends_at
        sub.auto_renew = False
        if not same_plan:
            sub.subscription_started_at = now
        db.add(sub)

    # ... flush → commit → refresh


class SubscriptionActivate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firm_id: int
    service_id: int
    plan_slug: str       # <-- plan by slug, no tier


# api/subscriptions/routes/service.py

from fastapi import APIRouter, File, UploadFile, status
from api.deps import AdminUser, CurrentUser, DBDep
from api.subscriptions.logics.service import (
    create_service,
    delete_service,
    read_service_with_plans,
    read_services,
    update_service,
)
from api.subscriptions.schemas import (
    CreateServiceResponse,
    DeleteServiceResponse,
    ServiceCreate,
    ServiceRead,
    ServiceUpdate,
    ServiceWithPlansRead,
    UpdateServiceResponse,
)


router = APIRouter(prefix="/services", tags=["Subscription Services"])




# api/subscriptions/routes/plan.py

from fastapi import APIRouter, status
from api.deps import AdminUser, DBDep
from api.subscriptions.logics.plan import (
    create_plan,
    delete_plan,
    read_plans_for_service,
    update_plan,
)
from api.subscriptions.schemas import (
    CreatePlanResponse,
    DeletePlanResponse,
    PlanCreate,
    PlanRead,
    PlanUpdate,
    UpdatePlanResponse,
)


router = APIRouter(tags=["Subscription Plans"])




# subscription

SubscriptionStatusLabel = Literal[
    "active",
    "trial",
    "trial_expired",
    "expired",
    "inactive",
    "unknown",
]


class SubscriptionRead(BaseModel):
    """
    A single subscription as returned to the frontend.

    Derived fields (`is_active`, `is_on_trial`, `days_remaining`,
    `status_label`, `can_access`) are computed by the builder from
    the raw row and the current time — the caller never assembles
    them inline.
    """
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

    subscription_started_at: datetime | None = None
    subscription_ends_at: datetime | None = None

    auto_renew: bool

    # Derived
    is_active: bool
    days_remaining: int | None = None
    status_label: SubscriptionStatusLabel = "unknown"
    can_access: bool

    created_at: datetime
    updated_at: datetime


class SubscriptionStatusRead(BaseModel):
    """
    Compact status payload for the "is my firm subscribed?" widget.
    """
    firm_id: int
    service_id: int
    service_name: str
    tier_name: str | None = None
    plan_name: str | None = None
    is_active: bool
    is_on_trial: bool
    days_remaining: int
    status_label: SubscriptionStatusLabel = "unknown"
    can_access: bool



class SubscriptionActivate(BaseModel):
    """
    Admin override: activate/extend a subscription without payment.
    """
    model_config = ConfigDict(extra="forbid")

    firm_id: int
    service_id: int
    tier_id: int
    plan_id: int



























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







