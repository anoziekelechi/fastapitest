async def read_services(
    db: AsyncSession,
    *,
    current_user: ReadUser | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[ServiceRead]:
    """
    List subscription services.

    Public users:
        - See active services only.
        - A service must have at least one active tier.
        - That tier must have at least one active plan.

    Administrators:
        - Can see all services, including inactive services.

    Args:
        db: Async database session.
        current_user: Authenticated user or None for public access.
        skip: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A list of ServiceRead objects.
    """
    admin = is_admin(current_user)

    query = (
        select(SubscriptionService)
        .order_by(col(SubscriptionService.name))
        .offset(skip)
        .limit(limit)
    )

    if not admin:
        # A service is buyable when an active tier contains
        # at least one active plan.
        buyable_exists = (
            select(SubscriptionTier.id)
            .join(
                SubscriptionPlan,
                col(SubscriptionPlan.tier_id)
                == col(SubscriptionTier.id),
            )
            .where(
                col(SubscriptionTier.service_id)
                == col(SubscriptionService.id),
                col(SubscriptionTier.is_active).is_(True),
                col(SubscriptionPlan.is_active).is_(True),
            )
            .correlate(SubscriptionService)
            .exists()
        )

        query = query.where(
            col(SubscriptionService.is_active).is_(True),
            buyable_exists,
        )

    try:
        result = await db.execute(query)
        services = result.scalars().all()

    except SQLAlchemyError as exc:
        raise database_error(
            "reading subscription services",
            exc,
        ) from exc

    return [
        to_service_read(service)
        for service in services
    ]





async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceWithTiersRead:
    """
    Read a subscription service with its tiers and plans.

    Public users:
        - Can access active services only.
        - Must have at least one active tier with an active plan.
        - See active tiers only.
        - See active plans only.

    Administrators:
        - Can access active and inactive services.
        - Can see active and inactive tiers.
        - Can see active and inactive plans.

    Args:
        slug: Subscription service slug.
        db: Async database session.
        current_user: Authenticated user or None for public access.

    Returns:
        ServiceWithTiersRead containing the service, tiers, and plans.
    """
    admin = is_admin(current_user)

    # 1. Retrieve the service.
    service = await get_service_by_slug(db, slug)

    # 2. Restrict public access to active, buyable services.
    if not admin:
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )

        buyable = await service_has_buyable(
            db,
            service_id(service),
        )

        if not buyable:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )

    # 3. Build the tier query.
    tier_query = (
        select(SubscriptionTier)
        .where(
            col(SubscriptionTier.service_id)
            == service_id(service)
        )
        .options(
            _load_relationship(SubscriptionTier.plans)
        )
        .order_by(
            col(SubscriptionTier.name)
        )
    )

    # Public users can see active tiers only.
    if not admin:
        tier_query = tier_query.where(
            col(SubscriptionTier.is_active).is_(True)
        )

    # 4. Retrieve the tiers.
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

    # 5. Build the response tiers and filter their plans.
    tier_reads: list[TierWithPlansRead] = []

    for tier in tiers:
        plans = sorted(
            tier.plans or [],
            key=lambda plan: plan.name,
        )

        visible_plans = [
            plan
            for plan in plans
            if admin or plan.is_active
        ]

        tier_read = TierWithPlansRead.model_validate(
            tier
        ).model_copy(
            update={
                "plans": [
                    to_plan_read(plan)
                    for plan in visible_plans
                ]
            }
        )

        tier_reads.append(tier_read)

    # 6. Build the complete service response.
    return ServiceWithTiersRead(
        **ServiceRead.model_validate(
            service
        ).model_dump(),
        tiers=tier_reads,
        )
