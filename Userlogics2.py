// src/types/payment/read.ts

export interface PaymentMethodRead {
  id: number;
  name: string;
  slug?: string | null;
  payment_logo_url?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AvailablePaymentMethodsRead {
  total: number;
  methods: PaymentMethodRead[];
}


// src/types/payment/create.ts

import type { PaymentMethodRead } from "./read";

export interface PaymentMethodCreate {
  name: string;
}

export interface CreatePaymentMethodResponse {
  message: string;
  payment_method: PaymentMethodRead;
}

// src/types/payment/update.ts

import type { PaymentMethodRead } from "./read";

export interface PaymentMethodUpdate {
  name?: string;
}

export interface UpdatePaymentMethodResponse {
  message: string;
  payment_method: PaymentMethodRead;
}

export * from "./read";
export * from "./create";
export * from "./update";



// src/types/user/contact.ts

export interface ContactAdminMessage {
  email: string;
  message: string;
}

export interface ContactAdminResponse {
  message: string;
}




// src/hooks/useAvailablePaymentMethods.ts

import { useEffect, useState } from "react";
import axios from "axios";

import api from "@/api/client";
import type { AvailablePaymentMethodsRead } from "@/types";

interface UseAvailablePaymentMethodsResult {
  data: AvailablePaymentMethodsRead | null;
  loading: boolean;
  error: string | null;
}

/**
 * Fetch every payment method accepted on the platform.
 *
 * Public endpoint — used by the Footer / landing page widget.
 */
export function useAvailablePaymentMethods(): UseAvailablePaymentMethodsResult {
  const [data, setData] = useState<AvailablePaymentMethodsRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let mounted = true;

    api
      .get<AvailablePaymentMethodsRead>("/payment-methods/available", {
        signal: controller.signal,
      })
      .then((res) => {
        if (mounted) setData(res.data);
      })
      .catch((err) => {
        if (axios.isCancel(err)) return;
        if (!mounted) return;

        if (!axios.isAxiosError(err)) {
          setError("An unexpected error occurred.");
          return;
        }

        const status = err.response?.status;
        const detail = err.response?.data?.detail;

        setError(
          typeof detail === "string"
            ? detail
            : `Request failed (${status ?? "unknown"}).`
        );
      })
      .finally(() => {
        if (mounted && !controller.signal.aborted) setLoading(false);
      });

    return () => {
      mounted = false;
      controller.abort();
    };
  }, []);

  return { data, loading, error };




          








# api/payments/schemas.py

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_payment_option(value: str) -> str:
    """Uppercase + collapse whitespace. Single source of truth for names."""
    import re
    if not value or not value.strip():
        raise ValueError("Payment method name cannot be empty")
    return re.sub(r"\s+", " ", value.strip()).upper()


# =============================================================================
# REQUEST
# =============================================================================

class PaymentMethodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=100)
    # country_id: int  ← removed

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return normalize_payment_option(v)


class PaymentMethodUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_payment_option(v)


# =============================================================================
# READ
# =============================================================================

class PaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str | None = None
    payment_logo_url: str | None = None
    created_at: datetime
    updated_at: datetime
    # country_id: int  ← removed


class AvailablePaymentMethodsRead(BaseModel):
    """All payment methods. Single-country mode — flat list."""
    total: int
    methods: list[PaymentMethodRead]


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreatePaymentMethodResponse(BaseModel):
    message: str
    payment_method: PaymentMethodRead


class UpdatePaymentMethodResponse(BaseModel):
    message: str
    payment_method: PaymentMethodRead


class MessageResponse(BaseModel):
    message: str




# api/payments/logics.py
"""Payment methods business logic — single-country mode."""

import logging

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.core.file_storage import (
    PAYMENT_LOGO,
    PAYMENT_LOGO_MAX_SIZE,
    delete_from_r2,
    get_public_url,
    handle_file_update,
)
from api.core.slug import generate_slug
from api.payments.models import PaymentMethods
from api.payments.schemas import (
    AvailablePaymentMethodsRead,
    PaymentMethodCreate,
    PaymentMethodRead,
    PaymentMethodUpdate,
)
from api.users.schemas import ReadUser

logger = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================

def to_payment_method_read(method: PaymentMethods) -> PaymentMethodRead:
    """Build a PaymentMethodRead with the logo URL resolved from its key."""
    read = PaymentMethodRead.model_validate(method)
    return read.model_copy(
        update={
            "payment_logo_url": (
                get_public_url(method.payment_logo_key)
                if method.payment_logo_key
                else None
            ),
        }
    )


def payment_method_id_of(method: PaymentMethods) -> int:
    if method.id is None:
        logger.error("Payment method row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )
    return method.id


async def get_payment_by_slug(
    db: AsyncSession,
    slug: str,
) -> PaymentMethods | None:
    """Fetch a payment method by slug."""
    try:
        normalized = slug.strip().lower()
        result = await db.execute(
            select(PaymentMethods).where(
                col(PaymentMethods.slug) == normalized
            )
        )
        return result.scalars().first()
    except Exception:
        logger.exception(
            "Failed to fetch payment method slug=%s", slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )


# =============================================================================
# CREATE — ADMIN ONLY
# =============================================================================

async def create_payment_method(
    data: PaymentMethodCreate,
    payment_logo: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a payment method.

    Admin only — enforced by the route's `admin: AdminUser` dependency.

    `payment_logo` is optional. Uploaded to R2 before the DB insert;
    cleaned up if the insert fails.
    """

    # ------------------------------------------------------------------
    # Uniqueness — name is globally unique now
    # ------------------------------------------------------------------
    try:
        existing = (
            await db.execute(
                select(PaymentMethods).where(
                    col(PaymentMethods.name) == data.name,
                )
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check existing payment method name=%s", data.name
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment method '{data.name}' already exists",
        )

    # ------------------------------------------------------------------
    # Upload logo (before DB write so we can clean up on failure)
    # ------------------------------------------------------------------
    new_logo_key: str | None = None
    if payment_logo is not None:
        new_logo_key = await handle_file_update(
            file=payment_logo,
            current_key=None,
            prefix=PAYMENT_LOGO,
            max_size=PAYMENT_LOGO_MAX_SIZE,
        )

    # ------------------------------------------------------------------
    # Slug — derived from name only
    # ------------------------------------------------------------------
    method_slug = generate_slug(data.name)

    method = PaymentMethods(
        name=data.name,
        slug=method_slug,
        payment_logo_key=new_logo_key,
    )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    try:
        db.add(method)
        await db.commit()
        await db.refresh(method)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create payment method name=%s", data.name
        )

        if new_logo_key:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up orphaned logo key=%s",
                    new_logo_key,
                )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    logger.info(
        "Payment method '%s' (slug=%s) created by admin user_id=%s",
        method.name,
        method.slug,
        current_user.id,
    )

    return {
        "message": f"Payment method '{method.name}' created successfully",
        "payment_method": to_payment_method_read(method),
    }


# =============================================================================
# READ — all (admin only)
# =============================================================================

async def read_all_payment_methods(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> AvailablePaymentMethodsRead:
    """
    Return every payment method.

    Admin-only — enforced by the route's `admin: AdminUser` dependency.
    """
    try:
        total: int = (
            await db.execute(
                select(func.count()).select_from(PaymentMethods)
            )
        ).scalar() or 0

        result = await db.execute(
            select(PaymentMethods)
            .order_by(col(PaymentMethods.name))
            .offset(skip)
            .limit(limit)
        )
        methods = result.scalars().all()
    except Exception:
        logger.exception("Failed to list payment methods")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        )

    return AvailablePaymentMethodsRead(
        total=total,
        methods=[to_payment_method_read(m) for m in methods],
    )


# =============================================================================
# READ — available (public, deduplicated — for the Footer widget)
# =============================================================================

async def read_available_payment_methods(
    db: AsyncSession,
) -> AvailablePaymentMethodsRead:
    """
    Return one instance of each unique payment method.

    Public — no authentication. Used by the public "We accept"
    widget on the Footer and landing page.

    In single-country mode, "deduplicated" is a no-op since names
    are globally unique. Kept for API stability if multi-country
    returns later.
    """
    try:
        result = await db.execute(
            select(PaymentMethods).order_by(col(PaymentMethods.name))
        )
        methods = result.scalars().all()
    except Exception:
        logger.exception("Failed to load available payment methods")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        )

    return AvailablePaymentMethodsRead(
        total=len(methods),
        methods=[to_payment_method_read(m) for m in methods],
    )


# =============================================================================
# READ — single (admin only)
# =============================================================================

async def read_single_payment_method(
    slug: str,
    db: AsyncSession,
) -> PaymentMethodRead:
    """Get a single payment method by slug. Admin-only via route."""
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )
    return to_payment_method_read(method)


# =============================================================================
# UPDATE — ADMIN ONLY
# =============================================================================

async def update_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    payment_logo: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update a payment method by slug.

    Admin only — enforced by the route's `admin: AdminUser` dependency.

    `name` and `payment_logo` are both optional. Passing a new logo
    replaces the old one; omitting it leaves the existing logo intact.
    """
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    if data.name is None and payment_logo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    updated_fields: list[str] = []

    # ------------------------------------------------------------------
    # Name change
    # ------------------------------------------------------------------
    if data.name is not None and data.name != method.name:
        try:
            existing = (
                await db.execute(
                    select(PaymentMethods).where(
                        col(PaymentMethods.name) == data.name,
                        col(PaymentMethods.id)
                        != payment_method_id_of(method),
                    )
                )
            ).scalars().first()
        except Exception:
            logger.exception(
                "Failed to check duplicate payment method name=%s",
                data.name,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update payment method. Please try again.",
            )

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Payment method '{data.name}' already exists",
            )

        old_slug = method.slug
        method.name = data.name
        method.slug = generate_slug(data.name)
        updated_fields.append("name")

        logger.info(
            "Payment method name changed: slug '%s' -> '%s'",
            old_slug,
            method.slug,
        )

    # ------------------------------------------------------------------
    # Logo replacement
    # ------------------------------------------------------------------
    old_logo_key = method.payment_logo_key
    new_logo_key: str | None = None

    if payment_logo is not None:
        try:
            new_logo_key = await handle_file_update(
                file=payment_logo,
                current_key=old_logo_key,
                prefix=PAYMENT_LOGO,
                max_size=PAYMENT_LOGO_MAX_SIZE,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Failed to upload new logo for payment method slug=%s",
                slug,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update payment method. Please try again.",
            )

        method.payment_logo_key = new_logo_key
        updated_fields.append("payment_logo")

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    try:
        db.add(method)
        await db.commit()
        await db.refresh(method)
    except Exception:
        await db.rollback()
        logger.exception("Failed to update payment method slug=%s", slug)

        if new_logo_key and new_logo_key != old_logo_key:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up new logo key=%s", new_logo_key
                )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update payment method. Please try again.",
        )

    if (
        new_logo_key
        and old_logo_key
        and new_logo_key != old_logo_key
    ):
        try:
            delete_from_r2(old_logo_key)
        except Exception:
            logger.exception(
                "Failed to delete old payment logo key=%s", old_logo_key
            )

    logger.info(
        "Payment method id=%s updated by admin user_id=%s (fields=%s)",
        payment_method_id_of(method),
        current_user.id,
        ",".join(updated_fields),
    )

    return {
        "message": f"Payment method '{method.name}' updated successfully",
        "payment_method": to_payment_method_read(method),
    }


# =============================================================================
# DELETE — ADMIN ONLY
# =============================================================================

async def delete_payment_method(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Delete a payment method by slug. Admin-only via route."""
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    method_name = method.name

    try:
        await db.delete(method)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete payment method slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete payment method. Please try again.",
        )

    logger.info(
        "Payment method '%s' deleted by admin user_id=%s",
        method_name,
        current_user.id,
    )

    return {
        "message": f"Payment method '{method_name}' deleted successfully"
    }



# api/payments/routes.py

from fastapi import APIRouter, File, Form, UploadFile, status

from api.core.database import DBDep
from api.payments.logics import (
    create_payment_method,
    delete_payment_method,
    read_all_payment_methods,
    read_available_payment_methods,
    read_single_payment_method,
    update_payment_method,
)
from api.payments.schemas import (
    AvailablePaymentMethodsRead,
    CreatePaymentMethodResponse,
    MessageResponse,
    PaymentMethodCreate,
    PaymentMethodRead,
    PaymentMethodUpdate,
    UpdatePaymentMethodResponse,
)
from api.users.deps import AdminUser

router = APIRouter(prefix="/payment-methods", tags=["Payment Methods"])


# =============================================================================
# PUBLIC
#
# Declared BEFORE /{slug} so `/available` doesn't get swallowed by
# the dynamic path.
# =============================================================================

@router.get(
    "/available",
    response_model=AvailablePaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List payment methods accepted (public)",
)
async def list_available_payment_methods(db: DBDep):
    """
    Public — no authentication.

    Used by the Footer widget and landing page to show the payment
    methods the platform supports.
    """
    return await read_available_payment_methods(db=db)


# =============================================================================
# ADMIN READS
# =============================================================================

@router.get(
    "",
    response_model=AvailablePaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List all payment methods (admin only)",
)
async def list_all_payment_methods(
    db: DBDep,
    admin: AdminUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_payment_methods(
        db=db, skip=skip, limit=limit
    )


@router.get(
    "/{slug}",
    response_model=PaymentMethodRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single payment method (admin only)",
)
async def get_payment_method(
    slug: str,
    db: DBDep,
    admin: AdminUser,
):
    return await read_single_payment_method(slug=slug, db=db)


# =============================================================================
# ADMIN WRITES — multipart (logo upload)
# =============================================================================

@router.post(
    "",
    response_model=CreatePaymentMethodResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment method (admin only)",
)
async def create_method(
    db: DBDep,
    admin: AdminUser,
    name: str = Form(...),
    payment_logo: UploadFile | None = File(None),
):
    data = PaymentMethodCreate(name=name)

    return await create_payment_method(
        data=data,
        payment_logo=payment_logo,
        db=db,
        current_user=admin,
    )


@router.patch(
    "/{slug}",
    response_model=UpdatePaymentMethodResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a payment method (admin only)",
)
async def patch_payment_method(
    slug: str,
    db: DBDep,
    admin: AdminUser,
    name: str | None = Form(None),
    payment_logo: UploadFile | None = File(None),
):
    data = PaymentMethodUpdate(name=name)

    return await update_payment_method(
        slug=slug,
        data=data,
        payment_logo=payment_logo,
        db=db,
        current_user=admin,
    )


@router.delete(
    "/{slug}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a payment method (admin only)",
)
async def remove_payment_method(
    slug: str,
    db: DBDep,
    admin: AdminUser,
):
    return await delete_payment_method(
        slug=slug, db=db, current_user=admin
)







async def handle_disabled_account(
    data: ContactAdminMessage,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Handle contact form from disabled users.

    Enumeration-safe: returns the same generic response whether the
    account exists or is disabled.

    Support email is `settings.mail_username` — single-country mode.
    When offices carry a support address, this can pick the first
    office's email instead.
    """
    user = await get_user_by_email(db, data.email)

    # Generic response — don't reveal account status
    if not user or not user.disabled:
        return {
            "message": (
                "If your account exists, your message has been sent "
                "to our support team."
            )
        }

    user_id = get_user_id(user)

    # ------------------------------------------------------------------
    # Rate limit — max N per window
    # ------------------------------------------------------------------
    rate_key = f"contact_admin_rate:{user_id}"
    count = await redis.incr(rate_key)

    if count == 1:
        await redis.expire(
            rate_key,
            int(
                timedelta(
                    minutes=settings.contact_rate_window
                ).total_seconds()
            ),
        )

    if count > settings.contact_rate_limit:
        ttl = await redis.ttl(rate_key)
        default_window = int(
            timedelta(
                minutes=settings.contact_rate_window
            ).total_seconds()
        )
        retry_after = (
            ttl if isinstance(ttl, int) and ttl > 0 else default_window
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    # ------------------------------------------------------------------
    # Capture primitives — user ORM is not safe to pass to the task
    # ------------------------------------------------------------------
    full_names = user.full_names

    # Support email — single-country mode uses settings.mail_username.
    # (Offices carry their own contact info but for a global
    # "contact admin" flow, mail_username is the right recipient.)
    support_email = settings.mail_username

    background_tasks.add_task(
        send_support_message,
        user_full_names=full_names,
        support_email=support_email,
        user_email=data.email,
        message=data.message,
        mailer=mailer,
    )

    logger.info(
        "Disabled user %s (id=%s) contacted support → %s",
        data.email,
        user_id,
        support_email,
    )

    return {
        "message": (
            "Your message has been sent to our support team. "
            "We will review your account and get back to you."
        )
    }






