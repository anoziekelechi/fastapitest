

async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceWithTiersRead:
    service = await get_service_by_slug(db, slug)
    is_admin = _is_admin(current_user)

    if not service.is_active and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    tier_query = select(SubscriptionTier).where(
        SubscriptionTier.service_id == _service_id(service)
    )

    if not is_admin:
        tier_query = tier_query.where(
            SubscriptionTier.is_active.is_(True)
        )

    tier_query = tier_query.options(
        selectinload(SubscriptionTier.plans)
    ).order_by(SubscriptionTier.name)

    try:
        result = await db.execute(tier_query)
        tiers = result.scalars().unique().all()
    except SQLAlchemyError as exc:
        raise _database_error("reading service tiers", exc) from exc

    tier_reads: list[TierWithPlansRead] = []

    for tier in tiers:
        plans = [
            to_plan_read(plan)
            for plan in sorted(tier.plans, key=lambda p: p.name)
            if is_admin or plan.is_active
        ]

        tier_reads.append(
            TierWithPlansRead(
                **TierRead.model_validate(tier).model_dump(),
                plans=plans,
            )
        )

    return ServiceWithTiersRead(
        **ServiceRead.model_validate(service).model_dump(),
        tiers=tier_reads,
    )

vs


async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceWithTiersRead:
    """
    service + tiers + plans for pricing page
    public callers see only active ties and plans

    """
    service = await get_service_by_slug(db, slug)
    #is_admin = _is_admin(current_user)

    if (
        not service.is_active 
        and (current_user is None or not current_user.is_admin)
        ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{slug} Subscription service not found.",
        )
      
    is_admin= _require_admin
    tier_query = await db.execute(
    select(SubscriptionTier)
    .where(SubscriptionTier.service_id == _service_id(service))
    .order_by(col(SubscriptionTier.name))
    )
    tiers = tier_query.scalars().all()
    if not is_admin:
        result = tiers.where(
            #Cannot access attribute "where" for class "Sequence[SubscriptionTier]"
            #Attribute "where" is unknownPylancereportAttributeAccessIssue

            #SubscriptionTier.is_active == True # no error if i do this
            #Cannot access attribute "is_" for class "bool"
            SubscriptionTier.is_active.is_(True) #
        )

    tier_query = tier_query.options( #Cannot access attribute "options" for class "Result[Tuple[SubscriptionTier]]"
  #Attribute "options" is unknown
        selectinload(SubscriptionTier.plans)
       #Argument of type "list[SubscriptionPlan]" cannot be assigned to parameter "keys" of 
       # type "_AttrType" in function "selectinload" 
    ).order_by(SubscriptionTier.name)

    try:
        result = await db.execute(tier_query)
        tiers = result.scalars().unique().all()
    except SQLAlchemyError as exc:
        raise _database_error("reading service tiers", exc) from exc

    tier_reads: list[TierWithPlansRead] = []

    for tier in tiers:
        plans = [
            to_plan_read(plan)
            for plan in sorted(tier.plans, key=lambda p: p.name)
            if is_admin or plan.is_active
        ]

        tier_reads.append(
            TierWithPlansRead(
                **TierRead.model_validate(tier).model_dump(),
                plans=plans,
            )
        )

    return ServiceWithTiersRead(
        **ServiceRead.model_validate(service).model_dump(),
        tiers=tier_reads,
    )


# got error too on this code



async def _count_subscriptions(
    db: AsyncSession,
    *,
    **filters: Any,#One or more keyword parameters must follow "*" parameterPylance
) -> int:
    try:
        query = select(func.count()).select_from(Subscription)

        for field_name, value in filters.items():
            query = query.where(
                getattr(Subscription, field_name) == value
            )

        result = await db.execute(query)
        return int(result.scalar_one())
    except SQLAlchemyError as exc:
        raise _database_error("counting subscriptions", exc) from exc

