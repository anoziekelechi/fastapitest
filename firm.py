






# only select those logics not above

"""Subscription operations: read, update, trial, activate."""

import logging
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from api.core.media import resolve_logo_url
from api.firms.logics import firm_id_of, require_firm_access
from api.models.firm import Firm
from api.models.subscription import (
    Subscription,
    SubscriptionPlan,
    SubscriptionService,
    SubscriptionTier,
)
from api.core.guards import guard_firm_state, guard_user_state
from api.subscriptions.logics.subscription import (
    as_utc,
    compute_status_label,
    to_subscription_list,
    to_subscription_read,
    utc_now,
)
from api.subscriptions.schemas import (
    SubscriptionActivate,
    SubscriptionRead,
    SubscriptionStatusRead,
    SubscriptionUpdate,
    TrialServiceListRead,
    TrialServiceRead,
)
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)



# =============================================================================
# LOADERS
# =============================================================================












"""Subscription builders and status computation."""

from datetime import datetime, timedelta, timezone

from api.models.subscription import Subscription
from api.subscriptions.schemas import (
    SubscriptionRead,
    SubscriptionStatusLabel,
)


# =============================================================================
# TIME HELPERS
# =============================================================================

def utc_now() -> datetime:
    """Timezone-aware UTC now — the single source of 'current time'."""
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """Normalize a datetime to UTC (aware if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)










"""Subscription schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)


# =============================================================================
# SUBSCRIPTION — READ
# =============================================================================

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


# =============================================================================
# SUBSCRIPTION — UPDATE
# =============================================================================

class SubscriptionUpdate(BaseModel):
    """Toggle auto-renew."""
    model_config = ConfigDict(extra="forbid")

    auto_renew: bool

    @model_validator(mode="after")
    def reject_explicit_null(self):
        if (
            "auto_renew" in self.model_fields_set
            and self.auto_renew is None
        ):
            raise ValueError("auto_renew cannot be null.")
        return self


# =============================================================================
# TRIAL — SELECTION FLOW
# =============================================================================

class TrialServiceRead(BaseModel):
    """
    A service the firm can still start a trial for.

    Returned by `GET /firms/{firm_id}/subscriptions/trials/available`.
    Services already trialled by this firm are excluded.
    """
    service_id: int
    service_name: str
    service_slug: str
    service_logo_url: str | None = None
    description: str | None = None
    trial_days: int
    price_from: Decimal | None = None  # cheapest active plan price


class TrialServiceListRead(BaseModel):
    firm_id: int
    total: int
    services: list[TrialServiceRead]


class TrialStartRequest(BaseModel):
    """Start a trial for one service."""
    model_config = ConfigDict(extra="forbid")

    service_id: int


# =============================================================================
# ADMIN ACTIVATION
# =============================================================================

class SubscriptionActivate(BaseModel):
    """
    Admin override: activate/extend a subscription without payment.
    """
    model_config = ConfigDict(extra="forbid")

    firm_id: int
    service_id: int
    tier_id: int
    plan_id: int


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class SubscriptionListRead(BaseModel):
    firm_id: int
    total: int
    subscriptions: list[SubscriptionRead]


class UpdateSubscriptionResponse(BaseModel):
    message: str
    subscription: SubscriptionRead


class StartTrialResponse(BaseModel):
    message: str
    subscription: SubscriptionRead


class ActivateSubscriptionResponse(BaseModel):
    message: str
    subscription: SubscriptionRead







