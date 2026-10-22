
# logics


import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from fastapi import HTTPException, status, UploadFile
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select,col

from api.core.settings import get_settings
from api.core.slug import generate_slug
from api.models.subscription import (
    Subscription,
    SubscriptionPlan,
    SubscriptionService,
    SubscriptionTier,
)
#from api.models.firm import Firm
from api.subscriptions.helpers import (
    get_subscription_status,
    is_unexpired,
    utc_now,
)
from api.subscription.schemas import (
    ActivateSubscriptionResponse,
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
    SubscriptionActivate,
    SubscriptionRead,
    SubscriptionStatusRead,
    SubscriptionUpdate,
    TierCreate,
    TierRead,
    TierUpdate,
    TierWithPlansRead,
    UpdatePlanResponse,
    UpdateServiceResponse,
    UpdateSubscriptionResponse,
    UpdateTierResponse,
)
from api.core.file_storage import (
    delete_from_r2, handle_file_update, 
    get_public_url,
    SERVICE_IMAGE,
    SERVICE_IMAGE_MAX_SIZE,
    delete_from_r2
    )
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Common error handling and conversion helpers
# ---------------------------------------------------------------------------

def _load_relationship(attr: Any):
    """
    Wrap `selectinload` with a type-hiding shim.

    SQLModel declares relationships as their Python type
    (e.g. `list[SubscriptionPlan]`), but SQLAlchemy's metaclass
    replaces them with `InstrumentedAttribute` at runtime. Pyright
    sees the Python type and rejects the call. This helper keeps the
    suppression localized to one place instead of every call site.
    """
    return selectinload(attr)



def _http_error(
    code: int,
    message: str,
) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def _database_error(
    operation: str,
    exc: SQLAlchemyError,
) -> HTTPException:
    logger.exception("Database failure during %s", operation, exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="A database error occurred. Please try again.",
    )


def _is_admin(user: ReadUser | None) -> bool:
    return bool(user and getattr(user, "is_admin", False))


def _service_id(service: SubscriptionService) -> int:
    if service.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The service has no database ID.",
        )
    return service.id


def _tier_id(tier: SubscriptionTier) -> int:
    if tier.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The tier has no database ID.",
        )
    return tier.id


def _plan_id(plan: SubscriptionPlan) -> int:
    if plan.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The plan has no database ID.",
        )
    return plan.id


def _subscription_id(sub: Subscription) -> int:
    if sub.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The subscription has no database ID.",
        )
    return sub.id


def to_plan_read(plan: SubscriptionPlan) -> PlanRead:
    """Build a PlanRead with currency supplied by application settings."""
    result = PlanRead.model_validate(plan)
    return result.model_copy(
        update={"currency": settings.currency_code}
    )

def to_service_read(service: SubscriptionService) -> ServiceRead:
    """Build a ServiceRead with the logo URL resolved from its key."""
    read = ServiceRead.model_validate(service)
    return read.model_copy(
        update={
            "service_logo_url": (
                get_public_url(service.service_logo_key)
                if service.service_logo_key
                else None
            ),
        }
    )

async def _service_has_buyable(
    db:AsyncSession,
    service_id: int,
)->bool:
    
    # returns True if service has at least one active tie with atleast one
    query = (
            select(SubscriptionTier.id)
            .join(
                SubscriptionPlan,
                col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),  
            )
            .where(
                col(SubscriptionTier.service_id) == service_id,
                col(SubscriptionTier.is_active).is_(True),
                col(SubscriptionPlan.is_active).is_(True),
            )
            .limit(1)
        )
    try:
        result = await db.execute(query)
        return result.scalars().first() is not None
    except SQLAlchemyError as exc:
        raise _database_error(
            "checking service availabilit",exc
        ) from exc


def _compute_subscription_read(
    sub: Subscription,
) -> SubscriptionRead:
    remaining, label, can_access = get_subscription_status(
        is_active=sub.is_active,
        is_on_trial=sub.is_on_trial,
        trial_ends_at=sub.trial_ends_at,
        subscription_ends_at=sub.subscription_ends_at,
    )

    return SubscriptionRead.model_validate(sub).model_copy(
        update={
            "days_remaining": remaining,
            "status_label": label,
            "can_access": can_access,
        }
    )


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

# already has this under firm guard state
# async def _require_firm_access(
#     db: AsyncSession,
#     *,
#     firm_id: int,
#     current_user: ReadUser,
# ) -> Firm:
#     """
#     Verify the firm exists and the caller owns it or is an administrator.

#     This assumes ReadUser.id is the user's primary key and Firm has an
#     owner/user relationship represented by user_id. If your Firm model uses
#     a different ownership column, change this one helper.
#     """
#     try:
#         firm = await db.get(Firm, firm_id)
#     except SQLAlchemyError as exc:
#         raise _database_error("checking firm access", exc) from exc

#     if firm is None:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail="Firm not found.",
#         )

#     if not _is_admin(current_user):
#         owner_id = getattr(firm, "user_id", None)
#         if owner_id is None or owner_id != current_user.id:
#             raise HTTPException(
#                 status_code=status.HTTP_403_FORBIDDEN,
#                 detail="You do not have access to this firm.",
#             )

#     return firm


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


# async def get_payment_by_slug(
#     db: AsyncSession,
#     slug: str,
# ) -> PaymentMethods | None:
#     """Fetch a payment method by its normalized slug."""

#     normalized = slug.strip().lower()

#     try:
#         result = await db.execute(
#             select(PaymentMethods).where(
#                 PaymentMethods.slug == normalized
#             )
#         )
#         return result.scalars().first()

#     except SQLAlchemyError as exc:
#         logger.exception(
#             "Database error while fetching payment method: slug=%s",
#             normalized,
#         )
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Failed to load payment method. Please try again.",
#         ) from exc

async def get_service_by_slug(
    db: AsyncSession,
    slug: str,
) -> SubscriptionService:
    try:
        result = await db.execute(
            select(SubscriptionService).where(
                SubscriptionService.slug == slug
            )
        )
        service = result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error("looking up a service by slug", exc) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    return service


async def get_tier_by_id(
    db: AsyncSession,
    tier_id: int,
) -> SubscriptionTier:
    try:
        result = await db.execute(
            select(SubscriptionTier).where(
                SubscriptionTier.id == tier_id
            )
        )
        tier = result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error("looking up a tier", exc) from exc

    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription tier not found.",
        )

    return tier


async def get_plan_by_id(
    db: AsyncSession,
    plan_id: int,
) -> SubscriptionPlan:
    try:
        result = await db.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.id == plan_id
            )
        )
        plan = result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error("looking up a plan", exc) from exc

    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription plan not found.",
        )

    return plan


async def get_subscription_by_firm_service(
    db: AsyncSession,
    firm_id: int,
    service_id: int,
) -> Subscription | None:
    try:
        result = await db.execute(
            select(Subscription).where(
                Subscription.firm_id == firm_id,
                Subscription.service_id == service_id,
            )
        )
        return result.scalars().first()
    except SQLAlchemyError as exc:
        raise _database_error(
            "looking up a firm's subscription",
            exc,
        ) from exc
# latest
async def _count_subscriptions(
    db: AsyncSession,
    **filters: Any,
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
# ---------------------------------------------------------------------------
# Service CRUD
# ---------------------------------------------------------------------------

async def create_service(
    data: ServiceCreate,
    service_image: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a service method.

    Admin access is enforced by the route's AdminUser dependency.

    service_image is optional,uploaded to R2 before the db write
    clean up if the DB fails
    """

    # ------------------------------------------------------------------
    # 1. Check for an existing payment method name
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(SubscriptionService).where(
                col(SubscriptionService.name) == data.name,
            
        )
        )
        existing = result.scalars().first()

    except SQLAlchemyError:
        await db.rollback()

        logger.exception(
            "Database error checking service method name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"sevice method '{data.name}' already exists.",
        )

    # ------------------------------------------------------------------
    # 2. Upload the optional image
    # ------------------------------------------------------------------
    new_image_key: str | None = None

    if service_image is not None:
        new_image_key = await handle_file_update(
            file=service_image,
            current_key=None,
            prefix=SERVICE_IMAGE,
            max_size=SERVICE_IMAGE_MAX_SIZE,
        )

    # ------------------------------------------------------------------
    # 3. Prepare the model
    # ------------------------------------------------------------------
    service = SubscriptionService(
        name=data.name,
        description=data.description,
        service_logo_key=new_image_key,
        trial_days=data.trial_days,
        is_active=True,
        slug=generate_slug(data.name),
    )

    # ------------------------------------------------------------------
    # 4. Best-effort cleanup for a failed database transaction
    # ------------------------------------------------------------------
    def cleanup_new_image() -> None:
        if new_image_key is None:
            return

        try:
            delete_from_r2(new_image_key)

        except Exception:
            logger.exception(
                "Failed to clean up service image in R2: key=%s",
                new_image_key,
            )

    # ------------------------------------------------------------------
    # 5. Persist the payment method
    # ------------------------------------------------------------------
    try:
        db.add(service)

        # Execute the INSERT and surface database constraints before commit.
        await db.flush()

        # Load database-generated values while the transaction is active.
        await db.refresh(service)

        # Commit only after the preceding operations succeed.
        await db.commit()

    except IntegrityError:
        await db.rollback()

        logger.warning(
            "service method creation violated a database integrity "
            "constraint: name=%s",
            data.name,
        )

        cleanup_new_image()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"service method '{data.name}' already exists "
                "or conflicts with a database constraint."
            ),
        )

    except SQLAlchemyError:
        await db.rollback()

        logger.exception(
            "Database error creating service method name=%s",
            data.name,
        )

        cleanup_new_image()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create service method. Please try again.",
        )

    except Exception:
        # Covers unexpected failures during persistence or refresh.
        # The transaction has not been committed if this block is reached.
        await db.rollback()

        logger.exception(
            "Unexpected error creating service method name=%s",
            data.name,
        )

        cleanup_new_image()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create service method. Please try again.",
        )

    # ------------------------------------------------------------------
    # 6. Log successful creation
    # ------------------------------------------------------------------
    logger.info(
        "service method '%s' (slug=%s) created by admin user_id=%s",
        service.name,
        service.slug,
        current_user.id,
    )

    # ------------------------------------------------------------------
    # 7. Return the response
    # ------------------------------------------------------------------
    return {
        "message": (
            f"Subscription service '{service.name}' created successfully."
        ),
        "service": to_service_read(service),
            }



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
    if not _is_admin(current_user):
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
        raise _database_error("reading subscription services", exc) from exc
    return [to_service_read(s) for s in service]
        

    

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
    if not _is_admin(current_user):
        # ACTIVE SERVICE REQUIRED
        if not service.is_active:
            raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{slug} Subscription service not found.",
            )
        # At least one buyable tier/plan pair required
        buyable = await _service_has_buyable(db, _service_id(service))
        if not buyable:
            raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{slug} Subscription service not found.",
            )
    return to_service_read(service)
            
   
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


# UPDATE SERVICE
async def update_service(
    slug: str,
    data: ServiceUpdate,
    db: AsyncSession,
    service_image: UploadFile | None = None,
) -> dict:
    """
    Update a subscription service.

    - Rejects an empty payload.
    - Updates only fields that actually changed.
    - Uploads a replacement image before touching the DB record.
    - Deletes the new upload after a failed flush only if rollback
      succeeds.
    - Preserves the new upload if commit raises, because its outcome
      may be uncertain.
    - Deletes the old image only after a confirmed successful commit.

    No IntegrityError handling is needed — this endpoint cannot violate
    a unique constraint (name and slug are not editable here).
    """

    # ---------------------------------------------------------
    # 1. Retrieve the existing service
    # ---------------------------------------------------------
    service = await get_service_by_slug(db=db, slug=slug)

    # ==============================================================
    # 2. Reject completely empty update payload
    # ==============================================================
    if all(
        value is None
        for value in (
            data.description,
            data.trial_days,
            service_image,
            data.is_active,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ---------------------------------------------------------
    # 3. Preserve the old image key
    # ---------------------------------------------------------
    old_image_key = service.service_logo_key
    new_image_key: str | None = None

    # ---------------------------------------------------------
    # 4. Upload a replacement image, if supplied
    # ---------------------------------------------------------
    if service_image is not None:
        try:
            new_image_key = await handle_file_update(
                file=service_image,
                current_key=None,
                prefix=SERVICE_IMAGE,
                max_size=SERVICE_IMAGE_MAX_SIZE,
            )

            if not new_image_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the service image.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Service image upload failed: service_id=%s",
                service.id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload service image.",
            ) from exc

    # ==============================================================
    # 5. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    if data.description is not None:
        if data.description != service.description:
            service.description = data.description
            updated_fields.append("description")

    if data.trial_days is not None:
        if data.trial_days != service.trial_days:
            service.trial_days = data.trial_days
            updated_fields.append("trial_days")

    if data.is_active is not None:
        if data.is_active != service.is_active:
            service.is_active = data.is_active
            updated_fields.append("is_active")

    if new_image_key is not None:
        if new_image_key != service.service_logo_key:
            service.service_logo_key = new_image_key
            updated_fields.append("service_logo_key")

    # ==============================================================
    # 6. Reject if nothing actually changed
    # ==============================================================
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ---------------------------------------------------------
    # 7. Flush before attempting commit
    #
    # No IntegrityError branch — this update cannot violate a
    # unique constraint. SQLAlchemyError and Exception cover
    # the realistic failure modes.
    # ---------------------------------------------------------
    try:
        await db.flush()

    except SQLAlchemyError as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after service flush error: "
                "service_id=%s",
                service.id,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "failure: service_id=%s, image_key=%s",
                    service.id,
                    new_image_key,
                )

        logger.exception(
            "Database error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            service.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update service. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    except Exception as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error: "
                "service_id=%s",
                service.id,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "failure: service_id=%s, image_key=%s",
                    service.id,
                    new_image_key,
                )

        logger.exception(
            "Unexpected error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            service.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating service.",
        ) from exc

    # ---------------------------------------------------------
    # 8. Commit the transaction
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # IMPORTANT:
        # Do not delete new_image_key here.
        #
        # A commit exception does not prove the database rejected
        # the transaction. The commit may have succeeded while its
        # acknowledgement was lost.
        #
        # Preserve the uploaded image until the database state can
        # be verified and any orphaned object reconciled.

        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "service_id=%s",
                service.id,
            )

        logger.exception(
            "Service commit failed or has an uncertain outcome. "
            "Preserving replacement image for reconciliation: "
            "service_id=%s, new_image_key=%s",
            service.id,
            new_image_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 9. Commit succeeded: refresh and delete the old image
    # ---------------------------------------------------------
    try:
        await db.refresh(service)
    except Exception:
        # Refresh failure after commit is not fatal — the update
        # is already persisted. Log and continue.
        logger.exception(
            "Failed to refresh service after update: service_id=%s",
            service.id,
        )

    if (
        new_image_key is not None
        and old_image_key is not None
        and old_image_key != new_image_key
    ):
        try:
            delete_from_r2(old_image_key)

        except Exception:
            # The database update has already succeeded.
            # Do not report the update as failed because old-image
            # cleanup failed; log it for later reconciliation.
            logger.exception(
                "Service updated, but the old image could not be "
                "deleted: service_id=%s, old_image_key=%s",
                service.id,
                old_image_key,
            )

    # ---------------------------------------------------------
    # 10. Return the updated service
    # ---------------------------------------------------------
    logger.info(
        "Service updated successfully: service_id=%s, fields=%s",
        service.id,
        ",".join(updated_fields),
    )

    return {
        "message": "Service updated successfully.",
        "service": to_service_read(service),
        }


async def delete_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    _require_admin(current_user)
    service = await get_service_by_slug(db, slug)

    count = await _count_subscriptions(
        db,
        service_id=_service_id(service),
    )
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This service cannot be deleted because subscriptions reference it.",
        )
    name=service.name
    try:
        await db.delete(service)
        await _commit_or_rollback(db, operation="deleting a service")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("deleting a service", exc) from exc

    logger.info(
        "Subscription service %s deleted by administrator %s",
        slug,
        current_user.id,
    )
    return {"message":f"{name} Subscription service deleted successfully."}


# ---------------------------------------------------------------------------
# Tier CRUD
# ---------------------------------------------------------------------------

async def create_tier(
    data: TierCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> CreateTierResponse:
    _require_admin(current_user)

    try:
        service = await db.get(SubscriptionService, data.service_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading a service for tier creation", exc) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    try:
        existing = await db.execute(
            select(SubscriptionTier).where(
                SubscriptionTier.service_id == data.service_id,
                func.lower(SubscriptionTier.name) == data.name.lower(),
            )
        )
        if existing.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A tier with this name already exists for the service.",
            )

        tier = SubscriptionTier(
            service_id=data.service_id,
            name=data.name,
            description=data.description,
            slug=generate_slug(
                f"{service.slug or service.name}-{data.name}"
            ),
            is_active=True,
        )
        db.add(tier)
        await _commit_or_rollback(db, operation="creating a tier")
        await _refresh_or_raise(db, tier, operation="refreshing a tier")

        return CreateTierResponse(
            message="Subscription tier created successfully.",
            tier=TierRead.model_validate(tier),
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("creating a tier", exc) from exc


async def read_tiers_for_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> list[TierRead]:
    service = await get_service_by_slug(db, slug)
    is_admin = _is_admin(current_user)

    if not service.is_active and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription service not found.",
        )

    query = select(SubscriptionTier).where(
        SubscriptionTier.service_id == _service_id(service)
    )

    if not is_admin:
        query = query.where(SubscriptionTier.is_active ==True)

    query = query.order_by(SubscriptionTier.name)

    try:
        result = await db.execute(query)
        return [
            TierRead.model_validate(item)
            for item in result.scalars().all()
        ]
    except SQLAlchemyError as exc:
        raise _database_error("reading service tiers", exc) from exc


async def read_tier_by_id(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> TierRead:
    tier = await get_tier_by_id(db, tier_id)
    is_admin = _is_admin(current_user)

    if not is_admin:
        if not tier.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription tier not found.",
            )

        service = await db.get(
            SubscriptionService,
            tier.service_id,
        )
        if service is None or not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription tier not found.",
            )

    return TierRead.model_validate(tier)


async def update_tier(
    tier_id: int,
    data: TierUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> UpdateTierResponse:
    _require_admin(current_user)
    tier = await get_tier_by_id(db, tier_id)
    changes = data.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided.",
        )

    changed = False
    for field_name, value in changes.items():
        if getattr(tier, field_name) != value:
            setattr(tier, field_name, value)
            changed = True

    if not changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made.",
        )

    await _commit_or_rollback(db, operation="updating a tier")
    await _refresh_or_raise(db, tier, operation="refreshing a tier")

    return UpdateTierResponse(
        message="Subscription tier updated successfully.",
        tier=TierRead.model_validate(tier),
    )


async def delete_tier(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    _require_admin(current_user)
    tier = await get_tier_by_id(db, tier_id)

    count = await _count_subscriptions(
        db,
        tier_id=_tier_id(tier),
    )
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This tier cannot be deleted because subscriptions reference it.",
        )

    try:
        await db.delete(tier)
        await _commit_or_rollback(db, operation="deleting a tier")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("deleting a tier", exc) from exc

    return {"message":"Subscription tier deleted successfully."}


# ---------------------------------------------------------------------------
# Plan CRUD
# ---------------------------------------------------------------------------

async def create_plan(
    data: PlanCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> CreatePlanResponse:
    _require_admin(current_user)

    try:
        tier = await db.get(SubscriptionTier, data.tier_id)
    except SQLAlchemyError as exc:
        raise _database_error("loading a tier for plan creation", exc) from exc

    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription tier not found.",
        )

    try:
        existing = await db.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.tier_id == data.tier_id,
                func.lower(SubscriptionPlan.name) == data.name.lower(),
            )
        )
        if existing.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A plan with this name already exists for the tier.",
            )

        plan = SubscriptionPlan(
            tier_id=data.tier_id,
            name=data.name,
            description=data.description,
            duration_days=data.duration_days,
            price=data.price,
            slug=generate_slug(
                f"{tier.slug or tier.name}-{data.name}"
            ),
            is_active=True,
        )
        db.add(plan)
        await _commit_or_rollback(db, operation="creating a plan")
        await _refresh_or_raise(db, plan, operation="refreshing a plan")

        return CreatePlanResponse(
            message="Subscription plan created successfully.",
            plan=to_plan_read(plan),
        )
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("creating a plan", exc) from exc


async def read_plans_for_tier(
    tier_id: int,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> list[PlanRead]:
    tier = await get_tier_by_id(db, tier_id)
    is_admin = _is_admin(current_user)

    if not is_admin:
        if not tier.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription tier not found.",
            )

        service = await db.get(
            SubscriptionService,
            tier.service_id,
        )
        if service is None or not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription tier not found.",
            )

    query = select(SubscriptionPlan).where(
        SubscriptionPlan.tier_id == _tier_id(tier)
    )

    if not is_admin:
        query = query.where(SubscriptionPlan.is_active ==True)

    query = query.order_by(col(SubscriptionPlan.duration_days))
    #Argument of type "int" cannot be assigned to parameter "__first" of type "_ColumnExpressionOrStrLabelArgument[Any] | Literal[_NoArg.NO_ARG] | None" in function "order_by"
    try:
        result = await db.execute(query)
        return [
            to_plan_read(plan)
            for plan in result.scalars().all()
        ]
    except SQLAlchemyError as exc:
        raise _database_error("reading tier plans", exc) from exc


async def read_plan_by_id(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> PlanRead:
    plan = await get_plan_by_id(db, plan_id)
    is_admin = _is_admin(current_user)

    if not is_admin:
        if not plan.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )

        tier = await db.get(SubscriptionTier, plan.tier_id)
        if tier is None or not tier.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )

        service = await db.get(
            SubscriptionService,
            tier.service_id,
        )
        if service is None or not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found.",
            )

    return to_plan_read(plan)


async def update_plan(
    plan_id: int,
    data: PlanUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> UpdatePlanResponse:
    _require_admin(current_user)
    plan = await get_plan_by_id(db, plan_id)
    changes = data.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided.",
        )

    changed = False
    for field_name, value in changes.items():
        if getattr(plan, field_name) != value:
            setattr(plan, field_name, value)
            changed = True

    if not changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made.",
        )

    await _commit_or_rollback(db, operation="updating a plan")
    await _refresh_or_raise(db, plan, operation="refreshing a plan")

    return UpdatePlanResponse(
        message="Subscription plan updated successfully.",
        plan=to_plan_read(plan),
    )


async def delete_plan(
    plan_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    _require_admin(current_user)
    plan = await get_plan_by_id(db, plan_id)

    count = await _count_subscriptions(
        db,
        plan_id=_plan_id(plan),
    )
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This plan cannot be deleted because subscriptions reference it.",
        )

    try:
        await db.delete(plan)
        await _commit_or_rollback(db, operation="deleting a plan")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _database_error("deleting a plan", exc) from exc

    return {"message":"Subscription plan deleted successfully."}
