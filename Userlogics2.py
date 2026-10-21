
from typing import Any, cast

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from sqlmodel import select


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

    tier_query = (
        select(SubscriptionTier)
        .where(
            SubscriptionTier.service_id == _service_id(service)
        )
    )

    if not is_admin:
        tier_query = tier_query.where(
            SubscriptionTier.is_active == True
        )

    tier_query = (
        tier_query
        .options(
            selectinload(
                cast(Any, SubscriptionTier.plans)
            )
        )
        .order_by(SubscriptionTier.name)
    )

    try:
        result = await db.execute(tier_query)
        tiers = result.scalars().unique().all()

    except SQLAlchemyError as exc:
        raise _database_error(
            "reading service tiers", exc
        ) from exc

    tier_reads: list[TierWithPlansRead] = []

    for tier in tiers:
        plans = [
            to_plan_read(plan)
            for plan in sorted(
                tier.plans,
                key=lambda plan: plan.name,
            )
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
