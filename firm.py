from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

router = APIRouter()


@router.get("/organizations/{org_id}/payment-methods", response_model=list[PaymentMethod])
def get_org_payment_methods(org_id: uuid.UUID, session: Session = Depends(get_session)):
    org = session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    statement = select(PaymentMethod).where(
        PaymentMethod.country == org.country,
        PaymentMethod.active == True,
    )
    return session.exec(statement).all()




"""Subscription service & plan schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from api.core.currency import Currency
from api.core.validators import normalize_field


# =============================================================================
# SERVICE — REQUEST
# =============================================================================

class ServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=50)
    description: str = Field(..., min_length=10, max_length=2000)
    trial_days: int = Field(default=30, ge=0, le=365)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        return normalize_field(v)


class ServiceUpdate(BaseModel):
    """
    Update a service.

    Mutable:
        - description
        - trial_days
        - is_active
        - service_logo (upload)

    Immutable:
        - name        (URLs and receipts key on it)
        - slug        (derived from name at creation)
    """
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(None, min_length=10, max_length=2000)
    trial_days: int | None = Field(None, ge=0, le=365)
    is_active: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        for field in ("description", "trial_days", "is_active"):
            if (
                field in self.model_fields_set
                and getattr(self, field) is None
            ):
                raise ValueError(f"{field} cannot be null.")
        return self


# =============================================================================
# PLAN — REQUEST
# =============================================================================

ALLOWED_DURATION_MONTHS = (1, 2, 3, 6, 12)


class PlanCreate(BaseModel):
    """
    Create a plan under a service.

    `service_id` is NOT here — the parent service is resolved from
    the URL (`POST /services/{service_slug}/plans`).
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=50)
    description: str | None = Field(None, max_length=2000)
    duration_months: int = Field(..., gt=0)
    price: Decimal = Field(..., gt=0, max_digits=10, decimal_places=2)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        return normalize_field(v)

    @field_validator("duration_months")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v not in ALLOWED_DURATION_MONTHS:
            raise ValueError(
                f"Duration must be one of "
                f"{', '.join(str(d) for d in ALLOWED_DURATION_MONTHS)} months."
            )
        return v


class PlanUpdate(BaseModel):
    """
    Update a plan.

    Mutable:
        - description
        - duration_months
        - price
        - is_active

    Immutable:
        - name        (URL and receipts key on it)
        - slug        (derived at creation)
        - service_id  (a plan never moves between services)
    """
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(None, max_length=2000)
    duration_months: int | None = Field(None, gt=0)
    price: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2)
    is_active: bool | None = None

    @field_validator("duration_months")
    @classmethod
    def validate_duration(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if v not in ALLOWED_DURATION_MONTHS:
            raise ValueError(
                f"Duration must be one of "
                f"{', '.join(str(d) for d in ALLOWED_DURATION_MONTHS)} months."
            )
        return v

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        for field in ("description", "duration_months", "price", "is_active"):
            if (
                field in self.model_fields_set
                and getattr(self, field) is None
            ):
                raise ValueError(f"{field} cannot be null.")
        return self


# =============================================================================
# PLAN — READ
# =============================================================================

class PlanRead(BaseModel):
    """Full plan shape."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_id: int
    name: str
    description: str | None = None
    slug: str
    duration_months: int
    price: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("price")
    def serialize_price(self, v: Decimal) -> str:
        return f"{v:.2f}"


class PlanSummaryRead(BaseModel):
    """Compact plan row for the service detail view."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    duration_months: int
    price: Decimal
    is_active: bool

    @field_serializer("price")
    def serialize_price(self, v: Decimal) -> str:
        return f"{v:.2f}"


# =============================================================================
# SERVICE — READ
# =============================================================================

class ServiceRead(BaseModel):
    """A service without plans."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str | None = None
    description: str
    service_logo_url: str | None = None
    is_active: bool
    trial_days: int
    created_at: datetime
    updated_at: datetime


class ServiceWithPlansRead(ServiceRead):
    """A service with its list of plans, sorted by name."""
    plans: list[PlanSummaryRead] = []


class ServicePlanNamesRead(ServiceRead):
    """
    Compact admin view: service + plan names (id + name + is_active).
    Used by pickers / dropdowns.
    """
    plans: list[PlanSummaryRead] = []


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreateServiceResponse(BaseModel):
    message: str
    service: ServiceRead


class UpdateServiceResponse(BaseModel):
    message: str
    service: ServiceRead


class DeleteServiceResponse(BaseModel):
    message: str
    service_name: str


class CreatePlanResponse(BaseModel):
    message: str
    plan: PlanRead


class UpdatePlanResponse(BaseModel):
    message: str
    plan: PlanRead


class DeletePlanResponse(BaseModel):
    message: str
    plan_name: str


# =============================================================================
# TRIAL — SELECTION FLOW (unchanged from before, but no tier)
# =============================================================================

class TrialServiceRead(BaseModel):
    service_id: int
    service_name: str
    service_slug: str
    service_logo_url: str | None = None
    description: str
    trial_days: int
    price_from: Decimal | None = None

    @field_serializer("price_from")
    def serialize_price_from(self, v: Decimal | None) -> str | None:
        return f"{v:.2f}" if v is not None else None


class TrialServiceListRead(BaseModel):
    firm_id: int
    total: int
    services: list[TrialServiceRead]


class TrialStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: int







"""Service business logic — no tiers, plans hang directly off services."""

import logging

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.core.media import resolve_logo_url
from api.core.file_storage import (
    SERVICE_IMAGE,
    SERVICE_IMAGE_MAX_SIZE,
    delete_from_r2,
    handle_file_update,
)
from api.core.slug import generate_slug
from api.models.subscription import (
    Subscription,
    SubscriptionPlan,
    SubscriptionService,
)
from api.subscriptions.schemas import (
    PlanSummaryRead,
    ServiceCreate,
    ServiceRead,
    ServiceUpdate,
    ServiceWithPlansRead,
)
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)


# =============================================================================
# ID NARROWING
# =============================================================================

def service_id_of(service: SubscriptionService) -> int:
    if service.id is None:
        logger.error(
            "SubscriptionService row has no id — data integrity issue",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        )
    return service.id


# =============================================================================
# LOADERS
# =============================================================================

async def get_service_by_slug(
    db: AsyncSession,
    slug: str,
) -> SubscriptionService:
    """Fetch a service by slug or raise 404."""
    normalized_slug = slug.strip().lower()
    try:
        result = await db.execute(
            select(SubscriptionService).where(
                col(SubscriptionService.slug) == normalized_slug,
            )
        )
        service = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch service slug=%s", normalized_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{slug}' not found.",
        )
    return service


async def get_service_by_name(
    db: AsyncSession,
    name: str,
    *,
    exclude_service_id: int | None = None,
) -> SubscriptionService | None:
    """Fetch a service by name, optionally excluding a specific id."""
    try:
        stmt = select(SubscriptionService).where(
            col(SubscriptionService.name) == name,
        )
        if exclude_service_id is not None:
            stmt = stmt.where(
                col(SubscriptionService.id) != exclude_service_id,
            )
        result = await db.execute(stmt)
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching service name=%s", name,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc


async def service_has_buyable(
    db: AsyncSession,
    service_id: int,
) -> bool:
    """
    True if a service has at least one active plan.

    Previously this joined tier → plan. Now plans hang directly
    off the service, so it's a single-table check.
    """
    try:
        result = await db.execute(
            select(SubscriptionPlan.id)
            .where(
                col(SubscriptionPlan.service_id) == service_id,
                col(SubscriptionPlan.is_active).is_(True),
            )
            .limit(1)
        )
        return result.scalars().first() is not None
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to check buyable status for service_id=%s",
            service_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc


async def count_subscriptions(
    db: AsyncSession,
    **filters,
) -> int:
    """Generic subscription counter. filters are column=value."""
    try:
        query = select(func.count()).select_from(Subscription)
        for field_name, value in filters.items():
            query = query.where(
                getattr(Subscription, field_name) == value,
            )
        result = await db.execute(query)
        return int(result.scalar_one())
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to count subscriptions: filters=%s", filters,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc


# =============================================================================
# BUILDERS — pure, no DB, no HTTP
# =============================================================================

def to_service_read(service: SubscriptionService) -> ServiceRead:
    """Assemble a ServiceRead with the logo URL resolved."""
    read = ServiceRead.model_validate(service)
    read.service_logo_url = resolve_logo_url(service.service_logo_key)
    return read


def to_plan_summary_read(plan: SubscriptionPlan) -> PlanSummaryRead:
    """Compact plan row for nested service views."""
    return PlanSummaryRead.model_validate(plan)


def to_service_with_plans_read(
    service: SubscriptionService,
    plans: list[SubscriptionPlan] | tuple[SubscriptionPlan, ...],
) -> ServiceWithPlansRead:
    """
    Assemble a service with its plans.

    `plans` is provided by the caller (already filtered/sorted) so
    the builder stays pure.
    """
    read = ServiceWithPlansRead.model_validate(service)
    read.service_logo_url = resolve_logo_url(service.service_logo_key)
    read.plans = [to_plan_summary_read(p) for p in plans]
    return read


def to_service_list(
    services: list[SubscriptionService] | tuple[SubscriptionService, ...],
) -> list[ServiceRead]:
    return [to_service_read(s) for s in services]


# =============================================================================
# LOADERS — multi-table reads
# =============================================================================

async def load_service_detail(
    db: AsyncSession,
    service: SubscriptionService,
    *,
    admin: bool,
) -> ServiceWithPlansRead:
    """
    Load plans for a service and build the detail response.

    - Admin: all plans, active or inactive.
    - Public: active plans only, and only if the service is buyable.
    """
    sid = service_id_of(service)

    if not admin:
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )
        if not await service_has_buyable(db, sid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )

    try:
        plan_query = (
            select(SubscriptionPlan)
            .where(col(SubscriptionPlan.service_id) == sid)
            .order_by(col(SubscriptionPlan.name))
        )
        if not admin:
            plan_query = plan_query.where(
                col(SubscriptionPlan.is_active).is_(True),
            )

        result = await db.execute(plan_query)
        plans = list(result.scalars().all())

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to read plans for service_id=%s", sid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

    return to_service_with_plans_read(service, plans)


# =============================================================================
# SERVICE — CREATE
# =============================================================================

async def create_service(
    data: ServiceCreate,
    service_image: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a service.

    - Name is globally unique — pre-checked, then enforced by the
      DB unique constraint as the race guard.
    - Image is uploaded before the DB row is written.
    - Image is deleted only after a confirmed rollback on failure.
    - On uncertain commit, the image is preserved for reconciliation.
    """
    existing = await get_service_by_name(db, data.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Service '{data.name}' already exists.",
        )

    new_image_key: str | None = None
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
                "Service image upload failed for name=%s", data.name,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload service image.",
            ) from exc

    service = SubscriptionService(
        name=data.name,
        description=data.description,
        service_logo_key=new_image_key,
        trial_days=data.trial_days,
        is_active=True,
        slug=generate_slug(data.name),
    )
    db.add(service)

    try:
        await db.flush()
    except IntegrityError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after service IntegrityError: name=%s",
                data.name,
            )
        if new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up image after IntegrityError: "
                    "name=%s, image_key=%s", data.name, new_image_key,
                )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Service '{data.name}' already exists "
                f"or conflicts with a database constraint."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after service flush error: name=%s",
                data.name,
            )
        if new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up image after flush error: "
                    "name=%s, image_key=%s", data.name, new_image_key,
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create service. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error: name=%s",
                data.name,
            )
        if new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up image after unexpected error: "
                    "name=%s, image_key=%s", data.name, new_image_key,
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating service.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: name=%s",
                data.name,
            )
        logger.exception(
            "Service commit failed or has an uncertain outcome. "
            "Preserving uploaded image for reconciliation: "
            "name=%s, new_image_key=%s", data.name, new_image_key,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(service)
    except Exception:
        logger.exception(
            "Failed to refresh service after create: name=%s", data.name,
        )

    logger.info(
        "Service '%s' (slug=%s) created by admin user_id=%s",
        service.name, service.slug, current_user.id,
    )

    return {
        "message": (
            f"Subscription service '{service.name}' created successfully."
        ),
        "service": to_service_read(service),
    }


# =============================================================================
# SERVICE — READ
# =============================================================================

async def read_services(
    db: AsyncSession,
    *,
    current_user: ReadUser | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[ServiceRead]:
    """
    List services.

    - Public: active services with at least one active plan.
    - Admin: all services.
    """
    admin = bool(current_user and getattr(current_user, "is_admin", False))

    query = (
        select(SubscriptionService)
        .order_by(col(SubscriptionService.name))
        .offset(skip)
        .limit(limit)
    )

    if not admin:
        buyable_exists = (
            select(SubscriptionPlan.id)
            .where(
                col(SubscriptionPlan.service_id)
                == col(SubscriptionService.id),
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
        services = list(result.scalars().all())
    except SQLAlchemyError as exc:
        logger.exception("Failed to list services")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load services. Please try again.",
        ) from exc

    return to_service_list(services)


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


# =============================================================================
# SERVICE — UPDATE
# =============================================================================

async def update_service(
    slug: str,
    data: ServiceUpdate,
    db: AsyncSession,
    service_image: UploadFile | None = None,
) -> dict:
    """
    Update a service (description, trial_days, is_active, image).

    - Rejects empty payloads and no-ops.
    - Upload replacement image before touching the DB.
    - Delete new image on rollback if flush fails; preserve on
      uncertain commit.
    - Delete old image only after confirmed commit.
    - No IntegrityError branch: name/slug immutable here.
    """
    service = await get_service_by_slug(db, slug)
    sid = service_id_of(service)

    if all(
        v is None
        for v in (data.description, data.trial_days, data.is_active, service_image)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    old_image_key = service.service_logo_key
    new_image_key: str | None = None

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
                "Service image upload failed: service_id=%s", sid,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload service image.",
            ) from exc

    updated_fields: list[str] = []

    if data.description is not None and data.description != service.description:
        service.description = data.description
        updated_fields.append("description")

    if data.trial_days is not None and data.trial_days != service.trial_days:
        service.trial_days = data.trial_days
        updated_fields.append("trial_days")

    if data.is_active is not None and data.is_active != service.is_active:
        service.is_active = data.is_active
        updated_fields.append("is_active")

    if new_image_key is not None and new_image_key != service.service_logo_key:
        service.service_logo_key = new_image_key
        updated_fields.append("service_logo_key")

    if not updated_fields:
        if new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up unused image after no-op: "
                    "service_id=%s, image_key=%s", sid, new_image_key,
                )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    try:
        await db.flush()
    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after service flush error: service_id=%s",
                sid,
            )
        if new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "error: service_id=%s, image_key=%s",
                    sid, new_image_key,
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update service. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error: service_id=%s",
                sid,
            )
        if new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after "
                    "unexpected error: service_id=%s, image_key=%s",
                    sid, new_image_key,
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating service.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: service_id=%s",
                sid,
            )
        logger.exception(
            "Service commit failed or has an uncertain outcome. "
            "Preserving replacement image: service_id=%s, new_image_key=%s",
            sid, new_image_key,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(service)
    except Exception:
        logger.exception(
            "Failed to refresh service after update: service_id=%s", sid,
        )

    if (
        new_image_key is not None
        and old_image_key is not None
        and old_image_key != new_image_key
    ):
        try:
            delete_from_r2(old_image_key)
        except Exception:
            logger.exception(
                "Service updated, but old image could not be deleted: "
                "service_id=%s, old_image_key=%s", sid, old_image_key,
            )

    logger.info(
        "Service updated: service_id=%s, fields=%s",
        sid, ",".join(updated_fields),
    )

    return {
        "message": "Service updated successfully.",
        "service": to_service_read(service),
    }


# =============================================================================
# SERVICE — DELETE
# =============================================================================

async def delete_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a service.

    - Refused if any subscription references it (FK restrict).
    - Deletes the image from R2 only after confirmed commit.
    - Preserves the image on uncertain commit.
    """
    service = await get_service_by_slug(db, slug)
    sid = service_id_of(service)
    name = service.name
    logo_key = service.service_logo_key

    count = await count_subscriptions(db, service_id=sid)
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This service cannot be deleted because "
                "subscriptions reference it."
            ),
        )

    try:
        await db.delete(service)
        await db.flush()
    except IntegrityError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after service IntegrityError: service_id=%s",
                sid,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Service '{name}' now has subscriptions and cannot "
                f"be deleted. Refresh and try again."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after service delete flush error: "
                "service_id=%s", sid,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to delete service. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after unexpected delete flush error: "
                "service_id=%s", sid,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting service.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: service_id=%s",
                sid,
            )
        logger.exception(
            "Service delete commit failed or has an uncertain outcome. "
            "Preserving image: service_id=%s, logo_key=%s", sid, logo_key,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service deletion could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    if logo_key is not None:
        try:
            delete_from_r2(logo_key)
        except Exception:
            logger.exception(
                "Service deleted, but its image could not be removed: "
                "service_id=%s, logo_key=%s", sid, logo_key,
            )

    logger.info(
        "Service '%s' (id=%s) deleted by admin user_id=%s",
        name, sid, current_user.id,
    )

    return {
        "message": f"Subscription service '{name}' deleted successfully."
    }





"""Plan business logic — plans live directly under services."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.core.slug import generate_slug
from api.models.subscription import (
    Subscription,
    SubscriptionPlan,
    SubscriptionService,
)
from api.subscriptions.logics.service import (
    get_service_by_slug,
    service_id_of,
)
from api.subscriptions.schemas import PlanCreate, PlanRead, PlanUpdate
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)


# =============================================================================
# ID NARROWING
# =============================================================================

def plan_id_of(plan: SubscriptionPlan) -> int:
    if plan.id is None:
        logger.error(
            "SubscriptionPlan row has no id — data integrity issue",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plan. Please try again.",
        )
    return plan.id


# =============================================================================
# LOADERS
# =============================================================================

async def get_plan_by_service_and_slug(
    db: AsyncSession,
    service_id: int,
    plan_slug: str,
) -> SubscriptionPlan:
    """Fetch a plan by (service_id, slug) or raise 404."""
    normalized_slug = plan_slug.strip().lower()
    try:
        result = await db.execute(
            select(SubscriptionPlan).where(
                col(SubscriptionPlan.service_id) == service_id,
                col(SubscriptionPlan.slug) == normalized_slug,
            )
        )
        plan = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch plan service_id=%s slug=%s",
            service_id, normalized_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plan. Please try again.",
        ) from exc

    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan '{plan_slug}' not found for this service.",
        )
    return plan


async def get_plan_by_service_and_name(
    db: AsyncSession,
    service_id: int,
    name: str,
) -> SubscriptionPlan | None:
    """Fetch a plan by (service_id, name) — for uniqueness checks."""
    try:
        result = await db.execute(
            select(SubscriptionPlan).where(
                col(SubscriptionPlan.service_id) == service_id,
                col(SubscriptionPlan.name) == name,
            )
        )
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch plan service_id=%s name=%s",
            service_id, name,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plan. Please try again.",
        ) from exc


async def count_subscriptions_for_plan(
    db: AsyncSession,
    plan_id: int,
) -> int:
    try:
        result = await db.execute(
            select(func.count())
            .select_from(Subscription)
            .where(col(Subscription.plan_id) == plan_id)
        )
        return result.scalar() or 0
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to count subscriptions for plan_id=%s", plan_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load plan. Please try again.",
        ) from exc


# =============================================================================
# BUILDERS
# =============================================================================

def to_plan_read(plan: SubscriptionPlan) -> PlanRead:
    return PlanRead.model_validate(plan)


def to_plan_list(
    plans: list[SubscriptionPlan] | tuple[SubscriptionPlan, ...],
) -> list[PlanRead]:
    return [to_plan_read(p) for p in plans]


# =============================================================================
# CREATE
# =============================================================================

async def create_plan(
    service_slug: str,
    data: PlanCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a plan under a service.

    URL: POST /services/{service_slug}/plans

    - Service must exist.
    - (service_id, name) is unique per service.
    - Slug is {service.slug}-{plan.name} — globally unique by
      construction.
    """
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    existing = await get_plan_by_service_and_name(
        db, service_id=sid, name=data.name,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A plan named '{data.name}' already exists "
                f"for this service."
            ),
        )

    plan = SubscriptionPlan(
        service_id=sid,
        name=data.name,
        description=data.description,
        duration_months=data.duration_months,
        price=data.price,
        slug=generate_slug(f"{service.slug or service.name}-{data.name}"),
        is_active=True,
    )
    db.add(plan)

    try:
        await db.flush()
    except IntegrityError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after plan IntegrityError: "
                "service_id=%s, name=%s", sid, data.name,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A plan named '{data.name}' (or its slug) "
                f"already exists."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after plan flush error: service_id=%s",
                sid,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create plan. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after unexpected plan flush error: "
                "service_id=%s", sid,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating plan.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "service_id=%s, name=%s", sid, data.name,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The plan creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(plan)
    except Exception:
        logger.exception(
            "Failed to refresh plan after create: service_id=%s", sid,
        )

    logger.info(
        "Plan '%s' (slug=%s) created for service_id=%s by user_id=%s",
        plan.name, plan.slug, sid, current_user.id,
    )

    return {
        "message": "Subscription plan created successfully.",
        "plan": to_plan_read(plan),
    }


# =============================================================================
# READ
# =============================================================================

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


# =============================================================================
# UPDATE
# =============================================================================

async def update_plan(
    service_slug: str,
    plan_slug: str,
    data: PlanUpdate,
    db: AsyncSession,
) -> dict:
    """
    Update a plan under a service.

    URL: PATCH /services/{service_slug}/plans/{plan_slug}
    """
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    plan = await get_plan_by_service_and_slug(
        db, service_id=sid, plan_slug=plan_slug,
    )
    pid = plan_id_of(plan)

    if all(
        v is None
        for v in (
            data.description, data.duration_months,
            data.price, data.is_active,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    updated_fields: list[str] = []

    if data.description is not None and data.description != plan.description:
        plan.description = data.description
        updated_fields.append("description")

    if (
        data.duration_months is not None
        and data.duration_months != plan.duration_months
    ):
        plan.duration_months = data.duration_months
        updated_fields.append("duration_months")

    if data.price is not None and data.price != plan.price:
        plan.price = data.price
        updated_fields.append("price")

    if data.is_active is not None and data.is_active != plan.is_active:
        plan.is_active = data.is_active
        updated_fields.append("is_active")

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    try:
        await db.flush()
    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after plan flush error: plan_id=%s", pid,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update plan. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error: plan_id=%s",
                pid,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating plan.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: plan_id=%s",
                pid,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The plan update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(plan)
    except Exception:
        logger.exception(
            "Failed to refresh plan after update: plan_id=%s", pid,
        )

    logger.info(
        "Plan updated: plan_id=%s, fields=%s",
        pid, ",".join(updated_fields),
    )

    return {
        "message": "Subscription plan updated successfully.",
        "plan": to_plan_read(plan),
    }


# =============================================================================
# DELETE
# =============================================================================

async def delete_plan(
    service_slug: str,
    plan_slug: str,
    db: AsyncSession,
) -> dict[str, str]:
    """
    Delete a plan under a service.

    URL: DELETE /services/{service_slug}/plans/{plan_slug}
    """
    service = await get_service_by_slug(db, service_slug)
    sid = service_id_of(service)

    plan = await get_plan_by_service_and_slug(
        db, service_id=sid, plan_slug=plan_slug,
    )
    pid = plan_id_of(plan)
    plan_name = plan.name

    count = await count_subscriptions_for_plan(db, pid)
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Plan '{plan_name}' cannot be deleted because "
                f"{count} "
                f"{'subscription references' if count == 1 else 'subscriptions reference'} "
                f"it."
            ),
        )

    try:
        await db.delete(plan)
        await db.flush()
    except IntegrityError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after plan IntegrityError: plan_id=%s", pid,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Plan '{plan_name}' now has subscriptions and "
                f"cannot be deleted. Refresh and try again."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after plan delete flush error: plan_id=%s",
                pid,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to delete plan. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after unexpected delete flush error: "
                "plan_id=%s", pid,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting plan.",
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: plan_id=%s", pid,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The plan deletion could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    logger.info("Plan '%s' (id=%s) deleted", plan_name, pid)

    return {
        "message": "Subscription plan deleted successfully.",
        "plan_name": plan_name,
     }




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


@router.post(
    "",
    response_model=CreateServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a subscription service (admin)",
)
async def create(
    data: ServiceCreate,
    db: DBDep,
    current_user: AdminUser,
    service_image: UploadFile | None = File(None),
):
    return await create_service(
        data=data, service_image=service_image,
        db=db, current_user=current_user,
    )


@router.get(
    "",
    response_model=list[ServiceRead],
    summary="List subscription services",
)
async def list_all(
    db: DBDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 50,
):
    return await read_services(
        db=db, current_user=current_user, skip=skip, limit=limit,
    )


@router.get(
    "/{slug}",
    response_model=ServiceWithPlansRead,
    summary="Get a service with its plans",
)
async def read_single(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_service_with_plans(
        slug=slug, db=db, current_user=current_user,
    )


@router.patch(
    "/{slug}",
    response_model=UpdateServiceResponse,
    summary="Update a service (admin)",
)
async def update(
    slug: str,
    data: ServiceUpdate,
    db: DBDep,
    current_user: AdminUser,
    service_image: UploadFile | None = File(None),
):
    return await update_service(
        slug=slug, data=data, db=db, service_image=service_image,
    )


@router.delete(
    "/{slug}",
    response_model=DeleteServiceResponse,
    summary="Delete a service (admin)",
)
async def delete(
    slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await delete_service(slug=slug, db=db, current_user=current_user)




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


@router.post(
    "/services/{service_slug}/plans",
    response_model=CreatePlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a plan under a service (admin)",
)
async def create(
    service_slug: str,
    data: PlanCreate,
    db: DBDep,
    current_user: AdminUser,
):
    return await create_plan(
        service_slug=service_slug,
        data=data, db=db, current_user=current_user,
    )


@router.get(
    "/services/{service_slug}/plans",
    response_model=list[PlanRead],
    summary="List plans for a service (admin)",
)
async def list_for_service(
    service_slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await read_plans_for_service(
        service_slug=service_slug, db=db, current_user=current_user,
    )


@router.patch(
    "/services/{service_slug}/plans/{plan_slug}",
    response_model=UpdatePlanResponse,
    summary="Update a plan (admin)",
)
async def update(
    service_slug: str,
    plan_slug: str,
    data: PlanUpdate,
    db: DBDep,
    current_user: AdminUser,
):
    return await update_plan(
        service_slug=service_slug,
        plan_slug=plan_slug,
        data=data, db=db,
    )


@router.delete(
    "/services/{service_slug}/plans/{plan_slug}",
    response_model=DeletePlanResponse,
    summary="Delete a plan (admin)",
)
async def delete(
    service_slug: str,
    plan_slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await delete_plan(
        service_slug=service_slug,
        plan_slug=plan_slug,
        db=db,
    )



















 






    

       
  




# 5. Endpoint: Automatically fills base_currency from org.country
@router.post("/organizations", response_model=Organization)
def create_organization(payload: OrganizationCreate, session: Session = Depends(get_session)):
    org = Organization(
        name=payload.name,
        country=payload.country,
        base_currency=COUNTRY_BASE_CURRENCY[payload.country],  # Auto-filled!
    )
    session.add(org)
    session.commit()
    session.refresh(org)
    return org








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







async def require_firm_access(
    db: AsyncSession,
    *,
    firm_id: int,
    current_user: ReadUser,
    require_active: bool = True,
    for_update: bool = False,
) -> Firm:
    """
    Load a firm and verify the caller can access it.

    Steps:
        1. Load the firm.
        2. Return 404 if it does not exist.
        3. Verify ownership or admin permission.
        4. Optionally reject disabled firms.
        5. Optionally acquire a row-level lock.

    Does not check user state.
    """
    from api.core.logging import logger

    try:
        if for_update:
            result = await db.execute(
                select(Firm)
                .where(Firm.id == firm_id)
                .with_for_update()
            )
            firm = result.scalar_one_or_none()
        else:
            firm = await db.get(Firm, firm_id)

    except SQLAlchemyError as exc:
        logger.exception("Failed to load firm id=%s", firm_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        ) from exc

    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firm not found.",
        )

    if not is_admin(current_user) and firm.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this firm.",
            headers={"X-Error-Code": "wrong_permission"},
        )

    if require_active:
        guard_firm_state(firm)

    return firm




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







