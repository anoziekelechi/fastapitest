
async def _commit_or_rollback(
    db: AsyncSession,
    *,
    operation: str,
) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("Constraint violation during %s", operation)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The operation conflicts with an existing record.",
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error(operation, exc) from exc


async def _refresh_or_raise(
    db: AsyncSession,
    instance: Any,
    *,
    operation: str,
) -> None:
    try:
        await db.refresh(instance)
    except SQLAlchemyError as exc:
        raise _database_error(operation, exc) from exc

# already enforce via routes
def _require_admin(current_user: ReadUser) -> None:
    if not _is_admin(current_user):
    #if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required.",
        )



def _database_error(
    operation: str,
    exc: SQLAlchemyError,
) -> HTTPException:
    logger.exception("Database failure during %s", operation, exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="A database error occurred. Please try again.",
    )




"""
     SubscriptionService model now has
    service_logo_key: str | None = Field(
    default=None,
    sa_column=Column(
        String(500),
        nullable=True,
    ),
    )
   SERVICE_IMAGE = "home/serviceimage" added this in file_storage.py
   SERVICE_IMAGE_MAX_SIZE = ? not yet added
    """
# add service_image optional in db same as we did in payment option

# let the logics flow like this
# create_service now has service_image field
# read_service -> to list active service with tiers and plans
# eg receipt -> starter -> 1 month 5$
# user click receipt in frontend backend returns all tiers eg starter,pro
# user clicks starter backend returns plans eg 1 month 5$ 

async def create_service(
    data: ServiceCreate,
    db: AsyncSession,
    #current_user: ReadUser,
) -> dict:
   # _require_admin(current_user) #will enforce it at route level via admin:AdminUser

    try:
        existing = await db.execute(
            select(SubscriptionService).where(
                col(SubscriptionService.name)#func.lower
                == data.name
            )
        )
        if existing.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A subscription service with this name already exists.",
            )

        service = SubscriptionService(
            name=data.name,
            description=data.description,
            slug=generate_slug(data.name),
            trial_days=data.trial_days,
            is_active=True,
        )
        db.add(service)
        await _commit_or_rollback(db, operation="creating a service")
        await _refresh_or_raise(
            db, service, operation="refreshing a new service"
        )
        return {
            "message":"Subscription service created successfully.",
            "service":ServiceRead.model_validate(service),
        }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("creating a service", exc) from exc

async def read_services(
    db: AsyncSession,
    *,
    current_user: ReadUser | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[ServiceRead]:
    """
    List all available active service with tiers and plans 
    to public view so they can subscribe
    eg Receipt->stater->1 month 5$, etc
    Admin sees all 
    
    """
    query = select(SubscriptionService).order_by(
        SubscriptionService.name
    )

    try:
        query = select(SubscriptionService).order_by(
        col(SubscriptionService.name)
        )
        if current_user is None or not current_user.is_admin:
            #show active service that has both tiers and plans  to public to subscribe
            query = query.where(
            col(SubscriptionService.is_active)==True
            )
        # show all service to admin
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        service=result.scalars().all()
        
        return [
            ServiceRead.model_validate(s)
            for s in service
        ]
    except SQLAlchemyError as exc:
        raise _database_error("reading subscription services", exc) from exc


async def read_service_by_slug(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceRead:
    service = await get_service_by_slug(db, slug)
    """
    non admin are allowed to access  active service with tiers and plans via slug
    if non admin try to access service that has no tiers and plans reject the request
    eg Receipt->stater->1 month 5$, etc
    Admin access all 
    
    """

    if( 
        not service.is_active # with tiers and plans  to public to subscribe
        and (current_user is None or not current_user.is_admin)
        ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{slug} Subscription service not found.",
        )

    return ServiceRead.model_validate(service)


# read_service -> to list active service with tiers and plans
# eg receipt -> starter -> 1 month 5$
# user click receipt in frontend backend returns all tiers eg starter,pro
# user clicks starter backend returns plans eg 1 month 5$
async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceWithTiersRead:
    """
    Read a subscription service with its tiers and plans.

    Public callers see only active services, tiers, and plans.
    Admins can see inactive services, tiers, and plans.
    """
    service = await get_service_by_slug(db, slug)
    is_admin = _is_admin(current_user)

    if not service.is_active and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    tier_query = (
        select(SubscriptionTier)
        .where(
            col(SubscriptionTier.service_id)
            == _service_id(service)
        )
    )

    if not is_admin:
        tier_query = tier_query.where(
            col(SubscriptionTier.is_active).is_(True)
        )

    tier_query = (
        tier_query
        .options(
            _load_relationship(SubscriptionTier.plans)
        )
        .order_by(col(SubscriptionTier.name))
    )

    try:
        result = await db.execute(tier_query)
        tiers = result.scalars().all()

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to read tiers for service slug=%s",
            slug,
        )
        raise _database_error(
            "reading service tiers",
            exc,
        ) from exc

    tier_reads: list[TierWithPlansRead] = []

    for tier in tiers:
        plans = sorted(
            tier.plans or [],
            key=lambda plan: plan.name,
        )

        visible_plans = [
            plan
            for plan in plans
            if is_admin or plan.is_active
        ]

        tier_reads.append(
            TierWithPlansRead.model_validate(tier).model_copy(
                update={
                    "plans": [
                        to_plan_read(plan)
                        for plan in visible_plans
                    ],
                }
            )
        )

    return ServiceWithTiersRead(
        **ServiceRead.model_validate(service).model_dump(),
        tiers=tier_reads,
    )


