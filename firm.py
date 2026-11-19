

async def setup_home_logic(
    db: AsyncSession,
    sitename: str | None = None,
    intro: str | None = None,
    logo_key: UploadFile | None = None,
    banner_key: UploadFile | None = None,
) -> dict:
    """
    Create or partially update the MAIN home configuration.
    """

    # ------------------------------------------------------------------
    # Load existing config
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(Home).where(Home.config_type == "MAIN")
        )
        current = result.scalars().first()
    except Exception:
        logger.exception("Failed to load MAIN home config")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load home configuration",
        )

    # =========================================================================
    # FIRST-TIME SETUP
    # =========================================================================
    if current is None:
        if sitename is None or not sitename.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Site name is required for initial home setup",
            )

        cleaned_sitename = sitename.strip()
        cleaned_intro = intro.strip() if intro is not None else None

        # Uploads happen BEFORE the DB insert so we can clean up on failure
        new_logo_key = await handle_file_update(
            file=logo_key, current_key=None,
            prefix=LOGO_FOLDER, max_size=LOGO_MAX_SIZE,
        )
        new_banner_key = await handle_file_update(
            file=banner_key, current_key=None,
            prefix=BANNER_FOLDER, max_size=BANNER_MAX_SIZE,
        )

        try:
            record = Home(
                config_type="MAIN",
                sitename=cleaned_sitename,
                intro=cleaned_intro,
                logo_key=new_logo_key,
                banner_key=new_banner_key,
            )
            db.add(record)
            await db.commit()
            await db.refresh(record)
        except Exception as exc:
            await db.rollback()
            logger.exception("Failed to create home configuration")

            # Best-effort cleanup of files uploaded before commit
            if new_logo_key:
                delete_from_r2(new_logo_key)
            if new_banner_key:
                delete_from_r2(new_banner_key)

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create home configuration",
            ) from exc

        return {
            "message":"Home configuration created successfully",
             "home": ReadHome(
                id=record.id,
                sitename=record.sitename,
                intro=record.intro,
                logo_key=get_public_url(record.logo_key),
                banner_key=get_public_url(record.banner_key),
            ),
        }

    # =========================================================================
    # PARTIAL UPDATE
    # =========================================================================
    old_logo_key = current.logo_key
    old_banner_key = current.banner_key

    new_logo_key: str | None = None
    new_banner_key: str | None = None

    try:
        if sitename is not None:
            cleaned = sitename.strip()
            if not cleaned:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Site name cannot be empty",
                )
            current.sitename = cleaned

        if intro is not None:
            current.intro = intro.strip()

        if logo_key is not None:
            new_logo_key = await handle_file_update(
                file=logo_key, current_key=old_logo_key,
                prefix=LOGO_FOLDER, max_size=LOGO_MAX_SIZE,
            )
            current.logo_key = new_logo_key

        if banner_key is not None:
            new_banner_key = await handle_file_update(
                file=banner_key, current_key=old_banner_key,
                prefix=BANNER_FOLDER, max_size=BANNER_MAX_SIZE,
            )
            current.banner_key = new_banner_key

        if (
            sitename is None
            and intro is None
            and logo_key is None
            and banner_key is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one field must be provided",
            )

        db.add(current)
        await db.commit()
        await db.refresh(current)

    except HTTPException:
        await db.rollback()
        # Validation failed AFTER uploads → remove orphans
        if new_logo_key and new_logo_key != old_logo_key:
            delete_from_r2(new_logo_key)
        if new_banner_key and new_banner_key != old_banner_key:
            delete_from_r2(new_banner_key)
        raise

    except Exception as exc:
        await db.rollback()
        logger.exception("Failed to update home configuration")

        if new_logo_key and new_logo_key != old_logo_key:
            delete_from_r2(new_logo_key)
        if new_banner_key and new_banner_key != old_banner_key:
            delete_from_r2(new_banner_key)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update home configuration",
        ) from exc

    # Delete old files ONLY after successful commit
    if new_logo_key and old_logo_key and new_logo_key != old_logo_key:
        delete_from_r2(old_logo_key)
    if new_banner_key and old_banner_key and new_banner_key != old_banner_key:
        delete_from_r2(old_banner_key)

    return {
        "message":"Home confiuration updated successfully",
        "home":ReadHome(
            id=current.id,
            sitename=current.sitename,
            intro=current.intro,
            logo_key=get_public_url(current.logo_key),
            banner_key=get_public_url(current.banner_key),
        ), 
    }




# =============================================================================
# PUBLIC HOME SETTINGS
# =============================================================================

async def get_home_settings_logic(db: AsyncSession) -> ReadHome:
    """Public — returns MAIN config or a safe default."""
    try:
        result = await db.execute(
            select(Home).where(Home.config_type == "MAIN")
        )
        home = result.scalars().first()
    except Exception:
        logger.exception("Failed to load home settings")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load home settings",
        )

    if home is None:
        return ReadHome(
            id=0,
            sitename="Ecommerce",
            intro="",
            logo_key=None,
            banner_key=None,
        )

    return ReadHome(
        id=home.id,
        sitename=home.sitename,
        intro=home.intro,
        logo_key=get_public_url(home.logo_key),
        banner_key=get_public_url(home.banner_key),
    )























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







