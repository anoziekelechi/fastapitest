implement this in any of the 3 logics and lets use only 1 logics
this 3 logics is not attainable to my goals

# any service selected here calls a function that filter the service name and its tiers and plans to 
#public assuming in read_service i see receipt clicking it in frontends redirect me to
# a function that filters tiers and plans for it

# then he will see this
# starter,1 month 5$,[buy now ],3 months 10$[buy now]
# pro ,1 month 10$ [buy now ]3 months 15$[buy now ]
# redirect to select payment optionswith listof available gateways 
# after selecting payment options it calls this initiate_subscription_purchase
# after read_service user lands here with slug assuming service/{debt}
# then he will see this
# starter,1 month 5$,[buy now ],3 months 10$[buy now]
# pro ,1 month 10$ [buy now ]3 months 15$[buy now ]
# redirect to select payment optionswith listof available gateways 
# after selecting payment options it calls this initiate_subscription_purchase
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
    This prevent showing service the user cant buy
    Admin sees all 
    
    """
    query = (
        select(SubscriptionService)
        .order_by(col(SubscriptionService.name))
        .offset(skip)
        .limit(limit)
    )
    if not is_admin(current_user):
        # Only service that are active And have at least one active
        # tier with at least one active plan
        buyable_exist = (
            select(SubscriptionTier.id)
            .join(
                SubscriptionPlan,
                col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),  
            )
            .where(
                col(SubscriptionTier.service_id)
                == col(SubscriptionTier.is_active).is_(True),
                col(SubscriptionPlan.is_active).is_(True),
            )
            .exists()
        )
        query = query.where(
            col(SubscriptionService.is_active).is_(True),
            buyable_exist,
        )
    try:
        result = await db.execute(query)
        service = result.scalars().all()
    except SQLAlchemyError as exc:
        raise database_error("reading subscription services", exc) from exc
    return [to_service_read(s) for s in service]
        

# after read_service user lands here with slug assuming service/{receipt}
# then he will see this
# starter,1 month 5$,[buy now ],3 months 10$[buy now]
# pro ,1 month 10$ [buy now ]3 months 15$[buy now ]
# redirect to select payment optionswith listof available gateways 
# after selecting payment options it calls this initiate_subscription_purchase
# after read_service user lands here with slug assuming service/{debt}
# then he will see this
# starter,1 month 5$,[buy now ],3 months 10$[buy now]
# pro ,1 month 10$ [buy now ]3 months 15$[buy now ]
# redirect to select payment optionswith listof available gateways 
# after selecting payment options it calls this initiate_subscription_purchase
async def read_tier_by_slug(
    service_slug: str,
    tier_slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> TierWithPlansRead:
    """
    Fetch a tier (and its plans) by service slug + tier slug.

    Public: 404 if the service is inactive/unbuyable, the tier is
    inactive, or the tier has no active plans.

    Admin: sees everything.
    """
    #is_admin = is_admin(current_user)

    # 1. Service must exist
    service = await get_service_by_slug(db, service_slug)

    if not is_admin(current_user):
        if not service.is_active:
            raise HTTPException(
                404, "Subscription tier not found."
            )
        buyable = await service_has_buyable(db, service_id(service))
        if not buyable:
            raise HTTPException(
                404, "Subscription tier not found."
            )

    # 2. Tier must exist AND belong to this service
    tier = await get_tier_by_slug(db, tier_slug)

    if tier.service_id != service_id(service):
        raise HTTPException(
            404, "Subscription tier not found."
        )

    if not is_admin and not tier.is_active:
        raise HTTPException(
            404, "Subscription tier not found."
        )
 
    # 3. Load plans
    plans_query = (
        select(SubscriptionPlan)
        .where(col(SubscriptionPlan.tier_id) == tier_id(tier))
        .order_by(col(SubscriptionPlan.duration_days))
    )
    if not is_admin:
        plans_query = plans_query.where(
            col(SubscriptionPlan.is_active).is_(True)
        )

    try:
        result = await db.execute(plans_query)
        plans = result.scalars().all()
    except SQLAlchemyError as exc:
        raise database_error("reading tier plans", exc) from exc

    if not is_admin and not plans:
        raise HTTPException(
            404, "Subscription tier not found."
        )

    return TierWithPlansRead.model_validate(tier).model_copy(
        update={"plans": [to_plan_read(p) for p in plans]}
    )



#read_service_by_slug and  read_service_with_tiers serve same purpose
# do we still need this since we have this read_tier_by_slug
async def read_service_by_slug(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceRead:
    
    """
    non admin are allowed to access  active service with tiers and plans via slug
    if non admin try to access service that has no tiers and plans reject the request
    eg Receipt->stater->1 month 5$, etc
    Admin access all 
    
    """
    service = await get_service_by_slug(db, slug)
    if not is_admin(current_user):
        # ACTIVE SERVICE REQUIRED
        if not service.is_active:
            raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{slug} Subscription service not found.",
            )
        # At least one buyable tier/plan pair required
        buyable = await service_has_buyable(db, service_id(service))
        if not buyable:
            raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{slug} Subscription service not found.",
            )
    return to_service_read(service)
            
  # do we still need this since  we ave this read_tier_by_slug
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
    #is_admin = is_admin(current_user)

    if not service.is_active and not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    tier_query = (
        select(SubscriptionTier)
        .where(
            col(SubscriptionTier.service_id)
            == service_id(service)
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
        raise database_error(
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
