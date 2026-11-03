"""Payment method business logic."""

import logging
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.core.file_storage import (
    PAYMENT_LOGO,
    PAYMENT_LOGO_MAX_SIZE,
    delete_from_r2,
    handle_file_update,
)
from api.core.media import resolve_logo_url
from api.core.slug import generate_slug
from api.models.payments import PaymentMethods, Receipt
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

def payment_method_id_of(method: PaymentMethods) -> int:
    """Narrow the ORM id to int or fail loudly on corrupt rows."""
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
    """Fetch a payment method by its normalized slug."""
    normalized = slug.strip().lower()

    try:
        result = await db.execute(
            select(PaymentMethods).where(
                col(PaymentMethods.slug) == normalized,
            )
        )
        return result.scalars().first()

    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching payment method: slug=%s",
            normalized,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        ) from exc


async def get_payment_by_name(
    db: AsyncSession,
    name: str,
    *,
    exclude_payment_id: int | None = None,
) -> PaymentMethods | None:
    """Fetch a payment method by name. Exclude self when updating."""
    try:
        stmt = select(PaymentMethods).where(
            col(PaymentMethods.name) == name,
        )
        if exclude_payment_id is not None:
            stmt = stmt.where(
                col(PaymentMethods.id) != exclude_payment_id,
            )
        result = await db.execute(stmt)
        return result.scalars().first()

    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching payment method name=%s", name,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        ) from exc


async def get_payment_by_provider_code(
    db: AsyncSession,
    provider_code: str,
    *,
    exclude_payment_id: int | None = None,
) -> PaymentMethods | None:
    """Fetch a payment method by provider_code."""
    try:
        stmt = select(PaymentMethods).where(
            col(PaymentMethods.provider_code) == provider_code,
        )
        if exclude_payment_id is not None:
            stmt = stmt.where(
                col(PaymentMethods.id) != exclude_payment_id,
            )
        result = await db.execute(stmt)
        return result.scalars().first()

    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching payment method "
            "provider_code=%s", provider_code,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        ) from exc


# =============================================================================
# BUILDERS — pure, no DB / no HTTP
# =============================================================================

def to_payment_method_read(method: PaymentMethods) -> PaymentMethodRead:
    """Assemble a PaymentMethodRead with the logo URL resolved."""
    read = PaymentMethodRead.model_validate(method)
    read.payment_logo_url = resolve_logo_url(method.payment_logo_key)
    return read


def to_payment_method_list(
    methods: list[PaymentMethods] | tuple[PaymentMethods, ...],
    *,
    total: int | None = None,
) -> AvailablePaymentMethodsRead:
    """
    Assemble the payment-method list envelope.

    Pass `total` explicitly for paginated queries so the value
    reflects the full result set, not the current page.
    """
    items = [to_payment_method_read(m) for m in methods]
    return AvailablePaymentMethodsRead(
        total=total if total is not None else len(items),
        methods=items,
    )


# =============================================================================
# CREATE — ADMIN ONLY (route-level)
# =============================================================================

async def create_payment_method(
    data: PaymentMethodCreate,
    payment_logo: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a payment method.

    - `name` and `provider_code` are globally unique. Both are
      pre-checked explicitly, and the DB unique constraints back
      the check against a concurrent-create race.
    - The logo is optional and stored in R2. It is deleted only
      after a *confirmed* rollback when flush/commit fails.
    """

    # ------------------------------------------------------------------
    # 1. Pre-check: name uniqueness
    # ------------------------------------------------------------------
    existing_name = await get_payment_by_name(db, data.name)
    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment method '{data.name}' already exists.",
        )

    # ------------------------------------------------------------------
    # 2. Pre-check: provider_code uniqueness
    # ------------------------------------------------------------------
    existing_provider = await get_payment_by_provider_code(
        db, data.provider_code,
    )
    if existing_provider is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Payment provider code '{data.provider_code}' "
                f"already exists."
            ),
        )

    # ------------------------------------------------------------------
    # 3. Upload the optional logo
    # ------------------------------------------------------------------
    new_logo_key: str | None = None

    if payment_logo is not None:
        try:
            new_logo_key = await handle_file_update(
                file=payment_logo,
                current_key=None,
                prefix=PAYMENT_LOGO,
                max_size=PAYMENT_LOGO_MAX_SIZE,
            )
            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the payment logo.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Payment logo upload failed for name=%s", data.name,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload payment method logo.",
            ) from exc

    # ------------------------------------------------------------------
    # 4. Prepare the model
    # ------------------------------------------------------------------
    method = PaymentMethods(
        name=data.name,
        slug=generate_slug(data.name),
        provider_code=data.provider_code,
        is_active=data.is_active,
        payment_logo_key=new_logo_key,
    )

    # ------------------------------------------------------------------
    # 5. Stage the insert
    # ------------------------------------------------------------------
    db.add(method)

    # ------------------------------------------------------------------
    # 6. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method IntegrityError: "
                "name=%s", data.name,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up logo after IntegrityError: "
                    "name=%s, logo_key=%s", data.name, new_logo_key,
                )

        logger.warning(
            "Payment method creation violated a database integrity "
            "constraint: name=%s, provider_code=%s, "
            "rollback_succeeded=%s",
            data.name, data.provider_code, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Payment method '{data.name}' already exists "
                f"or conflicts with a database constraint."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method flush error: "
                "name=%s", data.name,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up logo after flush error: "
                    "name=%s, logo_key=%s", data.name, new_logo_key,
                )

        logger.exception(
            "Database error while flushing new payment method: "
            "name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create payment method. "
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
                "Rollback failed after unexpected flush error: name=%s",
                data.name,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up logo after unexpected flush "
                    "error: name=%s, logo_key=%s",
                    data.name, new_logo_key,
                )

        logger.exception(
            "Unexpected error while flushing new payment method: "
            "name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating payment method.",
        ) from exc

    # ------------------------------------------------------------------
    # 7. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # Commit outcome is uncertain. Preserve the uploaded logo.
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: name=%s",
                data.name,
            )

        logger.exception(
            "Payment method commit failed or has an uncertain outcome. "
            "Preserving uploaded logo for reconciliation: "
            "name=%s, new_logo_key=%s", data.name, new_logo_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The payment method creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 8. Refresh (best-effort) and return
    # ------------------------------------------------------------------
    try:
        await db.refresh(method)
    except Exception:
        logger.exception(
            "Failed to refresh payment method after create: name=%s",
            data.name,
        )

    logger.info(
        "Payment method created: id=%s, name=%s, slug=%s, "
        "provider_code=%s, is_active=%s, admin_user_id=%s",
        method.id,
        method.name,
        method.slug,
        method.provider_code,
        method.is_active,
        current_user.id,
    )

    return {
        "message": (
            f"Payment method '{method.name}' created successfully."
        ),
        "payment_method": to_payment_method_read(method),
    }


# =============================================================================
# READ — ALL (admin) — active + inactive
# =============================================================================

async def read_all_payment_methods(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> AvailablePaymentMethodsRead:
    """Admin-only via route. Includes active + inactive."""
    try:
        total: int = (
            await db.execute(
                select(func.count()).select_from(PaymentMethods)
            )
        ).scalar_one()

        result = await db.execute(
            select(PaymentMethods)
            .order_by(col(PaymentMethods.name))
            .offset(skip)
            .limit(limit)
        )
        methods = list(result.scalars().all())

    except SQLAlchemyError as exc:
        logger.exception("Failed to list all payment methods")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        ) from exc

    return to_payment_method_list(methods, total=total)


# =============================================================================
# READ — AVAILABLE (public) — active only
# =============================================================================

async def read_available_payment_methods(
    db: AsyncSession,
) -> AvailablePaymentMethodsRead:
    """
    Public 'We accept' widget. Active methods only.

    Inactive methods remain in the DB and are visible to admins.
    """
    try:
        result = await db.execute(
            select(PaymentMethods)
            .where(col(PaymentMethods.is_active).is_(True))
            .order_by(col(PaymentMethods.name))
        )
        methods = list(result.scalars().all())

    except SQLAlchemyError as exc:
        logger.exception("Failed to load available payment methods")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        ) from exc

    return to_payment_method_list(methods)


# =============================================================================
# READ — SINGLE (admin)
# =============================================================================

async def read_single_payment_method(
    slug: str,
    db: AsyncSession,
) -> PaymentMethodRead:
    """Get a payment method by slug. Admin-only via route."""
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
    db: AsyncSession,
    payment_logo: UploadFile | None = None,
) -> dict:
    """
    Update a payment method (name, provider_code, is_active, logo).

    Contract — same as update_firm / update_service:
        - Empty payload rejected.
        - No-op rejected.
        - Upload replacement logo before touching the DB.
        - On flush failure, delete the new logo only if rollback
          succeeded.
        - On commit failure, preserve the new logo (uncertain
          outcome).
        - Delete the old logo only after confirmed commit.
    """

    # ------------------------------------------------------------------
    # 1. Resolve the existing method
    # ------------------------------------------------------------------
    method = await get_payment_by_slug(db=db, slug=slug)
    if method is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found.",
        )

    mid = payment_method_id_of(method)

    # ------------------------------------------------------------------
    # 2. Determine which fields were supplied
    # ------------------------------------------------------------------
    changes = data.model_dump(exclude_unset=True)

    new_name = changes.get("name")
    new_provider_code = changes.get("provider_code")
    new_is_active = changes.get("is_active")

    name_changed = (
        new_name is not None and new_name != method.name
    )
    provider_code_changed = (
        new_provider_code is not None
        and new_provider_code != method.provider_code
    )
    is_active_changed = (
        new_is_active is not None
        and new_is_active != method.is_active
    )
    logo_supplied = payment_logo is not None

    if not (
        name_changed
        or provider_code_changed
        or is_active_changed
        or logo_supplied
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were provided.",
        )

    # ------------------------------------------------------------------
    # 3. Pre-check name uniqueness
    # ------------------------------------------------------------------
    if name_changed and new_name is not None:
        existing = await get_payment_by_name(
            db, new_name, exclude_payment_id=mid,
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A payment method with this name already exists."
                ),
            )

    # ------------------------------------------------------------------
    # 4. Pre-check provider_code uniqueness
    # ------------------------------------------------------------------
    if provider_code_changed and new_provider_code is not None:
        existing = await get_payment_by_provider_code(
            db, new_provider_code, exclude_payment_id=mid,
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Payment provider code '{new_provider_code}' "
                    f"is already in use."
                ),
            )

    # ------------------------------------------------------------------
    # 5. Preserve old logo key
    # ------------------------------------------------------------------
    old_logo_key = method.payment_logo_key

    # ------------------------------------------------------------------
    # 6. Upload replacement logo, if supplied
    # ------------------------------------------------------------------
    new_logo_key: str | None = None

    if payment_logo is not None:
        try:
            new_logo_key = await handle_file_update(
                file=payment_logo,
                current_key=None,
                prefix=PAYMENT_LOGO,
                max_size=PAYMENT_LOGO_MAX_SIZE,
            )
            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the payment logo.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Payment logo upload failed: payment_method_id=%s", mid,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload payment method logo.",
            ) from exc

    # ------------------------------------------------------------------
    # 7. Apply changes to the ORM object
    # ------------------------------------------------------------------
    if name_changed and new_name is not None:
        method.name = new_name
        method.slug = generate_slug(new_name)

    if provider_code_changed and new_provider_code is not None:
        method.provider_code = new_provider_code

    if is_active_changed and new_is_active is not None:
        method.is_active = new_is_active

    if new_logo_key is not None:
        method.payment_logo_key = new_logo_key

    # ------------------------------------------------------------------
    # 8. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method IntegrityError: "
                "payment_method_id=%s", mid,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after "
                    "IntegrityError: payment_method_id=%s, logo_key=%s",
                    mid, new_logo_key,
                )

        logger.warning(
            "Integrity error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            mid, rollback_succeeded,
        )

        if not rollback_succeeded:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "The update failed and its database state could not "
                    "be safely recovered. Check the payment method state."
                ),
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The payment method conflicts with an existing record."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method flush error: "
                "payment_method_id=%s", mid,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush "
                    "error: payment_method_id=%s, logo_key=%s",
                    mid, new_logo_key,
                )

        logger.exception(
            "Database error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            mid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update payment method. "
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
                "payment_method_id=%s", mid,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after "
                    "unexpected flush error: "
                    "payment_method_id=%s, logo_key=%s",
                    mid, new_logo_key,
                )

        logger.exception(
            "Unexpected error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            mid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating payment method.",
        ) from exc

    # ------------------------------------------------------------------
    # 9. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # Do not delete new_logo_key — commit outcome uncertain.
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "payment_method_id=%s", mid,
            )

        logger.exception(
            "Payment method commit failed or has an uncertain outcome. "
            "Preserving replacement logo for reconciliation: "
            "payment_method_id=%s, new_logo_key=%s",
            mid, new_logo_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The payment method update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 10. Refresh (best-effort)
    # ------------------------------------------------------------------
    try:
        await db.refresh(method)
    except Exception:
        logger.exception(
            "Failed to refresh payment method after update: id=%s", mid,
        )

    # ------------------------------------------------------------------
    # 11. Delete old logo after confirmed commit
    # ------------------------------------------------------------------
    if (
        new_logo_key is not None
        and old_logo_key is not None
        and old_logo_key != new_logo_key
    ):
        try:
            delete_from_r2(old_logo_key)
        except Exception:
            logger.exception(
                "Payment method updated, but the old logo could not "
                "be deleted: payment_method_id=%s, old_logo_key=%s",
                mid, old_logo_key,
            )

    # ------------------------------------------------------------------
    # 12. Return
    # ------------------------------------------------------------------
    logger.info(
        "Payment method updated successfully: id=%s, "
        "provider_code=%s, is_active=%s",
        mid, method.provider_code, method.is_active,
    )

    return {
        "message": "Payment method updated successfully.",
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
    """
    Delete a payment method.

    - Refused if the method has been referenced by any receipt
      (FK is RESTRICT).
    - The friendly pre-check backs the FK against the concurrent-
      insert race.
    - The logo is deleted from R2 only after a confirmed commit.
    - On commit failure, the logo is preserved for reconciliation.
    """
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    mid = payment_method_id_of(method)
    method_name = method.name
    logo_key = method.payment_logo_key

    # ------------------------------------------------------------------
    # 1. Refuse if receipts reference this method
    # ------------------------------------------------------------------
    try:
        receipt_count: int = (
            await db.execute(
                select(func.count())
                .select_from(Receipt)
                .where(col(Receipt.payment_method_id) == mid)
            )
        ).scalar() or 0
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to count receipts for payment_method_id=%s", mid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete payment method. Please try again.",
        ) from exc

    if receipt_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Payment method '{method_name}' is referenced by "
                f"{receipt_count} "
                f"{'receipt' if receipt_count == 1 else 'receipts'}. "
                f"Deactivate it instead."
            ),
        )

    # ------------------------------------------------------------------
    # 2. Stage the delete + flush
    #
    # IntegrityError here = a receipt was inserted concurrently
    # after step 1. DB RESTRICT fires and we report a clean 409.
    # ------------------------------------------------------------------
    try:
        await db.delete(method)
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method IntegrityError: "
                "payment_method_id=%s", mid,
            )

        logger.warning(
            "Payment method delete blocked by FK constraint: "
            "payment_method_id=%s, rollback_succeeded=%s",
            mid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Payment method '{method_name}' is now referenced "
                f"by receipts and cannot be deleted. Refresh and try again."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method delete flush error: "
                "payment_method_id=%s", mid,
            )
        logger.exception(
            "Database error while flushing payment method delete: "
            "payment_method_id=%s, rollback_succeeded=%s",
            mid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to delete payment method. "
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
                "Rollback failed after unexpected delete flush error: "
                "payment_method_id=%s", mid,
            )
        logger.exception(
            "Unexpected error while flushing payment method delete: "
            "payment_method_id=%s, rollback_succeeded=%s",
            mid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting payment method.",
        ) from exc

    # ------------------------------------------------------------------
    # 3. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # Preserve logo — commit outcome uncertain.
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "payment_method_id=%s", mid,
            )
        logger.exception(
            "Payment method delete commit failed or has an uncertain "
            "outcome. Preserving logo for reconciliation: "
            "payment_method_id=%s, logo_key=%s", mid, logo_key,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The payment method deletion could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 4. Commit succeeded: delete the logo from R2
    # ------------------------------------------------------------------
    if logo_key is not None:
        try:
            delete_from_r2(logo_key)
        except Exception:
            # DB state is final. A failed R2 delete must not turn
            # a successful delete into a 500.
            logger.exception(
                "Payment method deleted, but its logo could not be "
                "removed: payment_method_id=%s, logo_key=%s",
                mid, logo_key,
            )

    logger.info(
        "Payment method '%s' deleted by admin user_id=%s",
        method_name, current_user.id,
    )

    return {
        "message": f"Payment method '{method_name}' deleted successfully"
    }




class PaymentMethodUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    is_active: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(
        cls, v: str | None, info: FieldValidationInfo,
    ) -> str | None:
        if v is None:
            return None
        raw_name = info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_payment_option(v, field_name=display_name)

    @model_validator(mode="after")
    def reject_explicit_null_name(self):
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Payment name cannot be null.")
        return self


async def update_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    db: AsyncSession,
    payment_logo: UploadFile | None = None,
) -> dict:
    """
    Update a payment method.

    Mutable:
        - name        (regenerates slug)
        - is_active
        - payment logo

    Immutable (internal, set at creation only):
        - provider_code  (identifies the gateway integration;
                          changing it would break reconciliation)
        - slug           (server-managed; regenerated on name change)

    Contract — same as update_firm / update_service:
        - Empty payload rejected.
        - No-op rejected.
        - Upload replacement logo before touching the DB.
        - On flush failure, delete the new logo only if rollback
          succeeded.
        - On commit failure, preserve the new logo (uncertain
          outcome).
        - Delete the old logo only after confirmed commit.
    """

    # ------------------------------------------------------------------
    # 1. Resolve the existing method
    # ------------------------------------------------------------------
    method = await get_payment_by_slug(db=db, slug=slug)
    if method is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found.",
        )

    mid = payment_method_id_of(method)

    # ------------------------------------------------------------------
    # 2. Determine which fields were supplied
    # ------------------------------------------------------------------
    changes = data.model_dump(exclude_unset=True)

    new_name = changes.get("name")
    new_is_active = changes.get("is_active")

    name_changed = (
        new_name is not None and new_name != method.name
    )
    is_active_changed = (
        new_is_active is not None
        and new_is_active != method.is_active
    )
    logo_supplied = payment_logo is not None

    if not (name_changed or is_active_changed or logo_supplied):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were provided.",
        )

    # ------------------------------------------------------------------
    # 3. Pre-check name uniqueness
    # ------------------------------------------------------------------
    if name_changed and new_name is not None:
        existing = await get_payment_by_name(
            db, new_name, exclude_payment_id=mid,
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A payment method with this name already exists."
                ),
            )

    # ------------------------------------------------------------------
    # 4. Preserve old logo key
    # ------------------------------------------------------------------
    old_logo_key = method.payment_logo_key

    # ------------------------------------------------------------------
    # 5. Upload replacement logo, if supplied
    # ------------------------------------------------------------------
    new_logo_key: str | None = None

    if payment_logo is not None:
        try:
            new_logo_key = await handle_file_update(
                file=payment_logo,
                current_key=None,
                prefix=PAYMENT_LOGO,
                max_size=PAYMENT_LOGO_MAX_SIZE,
            )
            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the payment logo.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Payment logo upload failed: payment_method_id=%s", mid,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload payment method logo.",
            ) from exc

    # ------------------------------------------------------------------
    # 6. Apply changes
    # ------------------------------------------------------------------
    if name_changed and new_name is not None:
        method.name = new_name
        method.slug = generate_slug(new_name)

    if is_active_changed and new_is_active is not None:
        method.is_active = new_is_active

    if new_logo_key is not None:
        method.payment_logo_key = new_logo_key

    # ------------------------------------------------------------------
    # 7. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method IntegrityError: "
                "payment_method_id=%s", mid,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after "
                    "IntegrityError: payment_method_id=%s, logo_key=%s",
                    mid, new_logo_key,
                )

        logger.warning(
            "Integrity error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            mid, rollback_succeeded,
        )

        if not rollback_succeeded:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "The update failed and its database state could not "
                    "be safely recovered. Check the payment method state."
                ),
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The payment method conflicts with an existing record."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method flush error: "
                "payment_method_id=%s", mid,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush "
                    "error: payment_method_id=%s, logo_key=%s",
                    mid, new_logo_key,
                )

        logger.exception(
            "Database error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            mid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update payment method. "
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
                "payment_method_id=%s", mid,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after "
                    "unexpected flush error: "
                    "payment_method_id=%s, logo_key=%s",
                    mid, new_logo_key,
                )

        logger.exception(
            "Unexpected error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            mid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating payment method.",
        ) from exc

    # ------------------------------------------------------------------
    # 8. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # Do not delete new_logo_key — commit outcome uncertain.
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "payment_method_id=%s", mid,
            )

        logger.exception(
            "Payment method commit failed or has an uncertain outcome. "
            "Preserving replacement logo for reconciliation: "
            "payment_method_id=%s, new_logo_key=%s",
            mid, new_logo_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The payment method update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 9. Refresh (best-effort)
    # ------------------------------------------------------------------
    try:
        await db.refresh(method)
    except Exception:
        logger.exception(
            "Failed to refresh payment method after update: id=%s", mid,
        )

    # ------------------------------------------------------------------
    # 10. Delete old logo after confirmed commit
    # ------------------------------------------------------------------
    if (
        new_logo_key is not None
        and old_logo_key is not None
        and old_logo_key != new_logo_key
    ):
        try:
            delete_from_r2(old_logo_key)
        except Exception:
            logger.exception(
                "Payment method updated, but the old logo could not "
                "be deleted: payment_method_id=%s, old_logo_key=%s",
                mid, old_logo_key,
            )

    # ------------------------------------------------------------------
    # 11. Return
    # ------------------------------------------------------------------
    logger.info(
        "Payment method updated successfully: id=%s, "
        "provider_code=%s, is_active=%s",
        mid, method.provider_code, method.is_active,
    )

    return {
        "message": "Payment method updated successfully.",
        "payment_method": to_payment_method_read(method),
    }




#use this 
async def get_payment_by_provider_code(
    db: AsyncSession,
    provider_code: str,
) -> PaymentMethods | None:
    """Fetch a payment method by provider_code. Used only at create."""
    try:
        result = await db.execute(
            select(PaymentMethods).where(
                col(PaymentMethods.provider_code) == provider_code,
            )
        )
        return result.scalars().first()

    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching payment method "
            "provider_code=%s", provider_code,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        ) from exc




"""Payment method routes."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    UploadFile,
    status,
)

from api.deps import DBDep, CurrentUser, AdminUser
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


router = APIRouter(prefix="/payments", tags=["Payments"])


# =============================================================================
# CREATE — ADMIN ONLY
# =============================================================================

@router.post(
    "/add",
    response_model=CreatePaymentMethodResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment method (admin)",
)
async def create(
    db: DBDep,
    current_user: AdminUser,
    name: Annotated[str, Form(min_length=2, max_length=100)],
    provider_code: Annotated[str, Form(min_length=2, max_length=100)],
    is_active: Annotated[bool, Form()] = True,
    payment_logo: UploadFile | None = File(None),
):
    data = PaymentMethodCreate(
        name=name,
        provider_code=provider_code,
        is_active=is_active,
    )
    return await create_payment_method(
        data=data,
        payment_logo=payment_logo,
        db=db,
        current_user=current_user,
    )


# =============================================================================
# READ — ALL (admin, active + inactive)
# =============================================================================

@router.get(
    "/all",
    response_model=AvailablePaymentMethodsRead,
    summary="List all payment methods (admin)",
)
async def read_all(
    db: DBDep,
    current_user: AdminUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    return await read_all_payment_methods(db=db, skip=skip, limit=limit)


# =============================================================================
# READ — AVAILABLE (public, active only)
# =============================================================================

@router.get(
    "/available",
    response_model=AvailablePaymentMethodsRead,
    summary="List available payment methods (public)",
)
async def read_available(db: DBDep):
    return await read_available_payment_methods(db=db)


# =============================================================================
# READ — SINGLE (admin)
# =============================================================================

@router.get(
    "/{slug}",
    response_model=PaymentMethodRead,
    summary="Get a payment method by slug (admin)",
)
async def read_single(
    slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await read_single_payment_method(slug=slug, db=db)


# =============================================================================
# UPDATE — ADMIN ONLY
#
# Only name, is_active, and the logo are updatable.
# provider_code is intentionally NOT a form field: it identifies the
# gateway integration and is set at creation only. Sending it here
# would raise 422 (PaymentMethodUpdate has extra="forbid").
# 


# =============================================================================
# DELETE — ADMIN ONLY
# =============================================================================

@router.delete(
    "/{slug}",
    response_model=MessageResponse,
    summary="Delete a payment method (admin)",
)
async def remove(
    slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await delete_payment_method(
        slug=slug,
        db=db,
        current_user=current_user,
                                         )




@router.patch(
    "/{slug}",
    response_model=UpdatePaymentMethodResponse,
    summary="Update a payment method (admin)",
    description=(
        "Update mutable fields only: `name`, `is_active`, "
        "`payment_logo`. `provider_code` is immutable — it "
        "identifies the gateway integration and is set at creation. "
        "Sending it will be ignored."
    ),
)
async def update(
    slug: str,
    db: DBDep,
    current_user: AdminUser,
    name: Annotated[
        str | None, Form(min_length=2, max_length=100)
    ] = None,
    is_active: Annotated[bool | None, Form()] = None,
    payment_logo: UploadFile | None = File(None),
):
    data = PaymentMethodUpdate(name=name, is_active=is_active)
    return await update_payment_method(
        slug=slug,
        data=data,
        db=db,
        payment_logo=payment_logo,
    )






"""Admin schemas."""

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core.core_schema import FieldValidationInfo

from api.core.validators import normalize_field


# =============================================================================
# GROUP — REQUEST
# =============================================================================

class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=100)
    permission: str = Field(..., min_length=2, max_length=100)

    @field_validator("name", "permission", mode="before")
    @classmethod
    def validate_fields(
        cls, v: str, info: FieldValidationInfo,
    ) -> str:
        return normalize_field(v)


class GroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=2, max_length=100)
    permission: str | None = Field(None, min_length=2, max_length=100)

    @field_validator("name", "permission", mode="before")
    @classmethod
    def validate_fields(
        cls, v: str | None, info: FieldValidationInfo,
    ) -> str | None:
        if v is None:
            return None
        return normalize_field(v)

    @model_validator(mode="after")
    def reject_explicit_null(self):
        for field in ("name", "permission"):
            if (
                field in self.model_fields_set
                and getattr(self, field) is None
            ):
                raise ValueError(f"{field} cannot be null.")
        return self


class GroupAction(BaseModel):
    """Delete or otherwise act on a group by slug."""
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., min_length=2, max_length=120)


# =============================================================================
# GROUP — READ
# =============================================================================

class GroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    permission: str
    slug: str | None = None
    created_at: datetime
    updated_at: datetime


class GroupListResponse(BaseModel):
    total: int
    groups: list[GroupRead]


# =============================================================================
# USER ACTION — REQUEST
# =============================================================================

class UserGroupAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    group_name: str = Field(..., min_length=2, max_length=100)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()

    @field_validator("group_name", mode="before")
    @classmethod
    def normalize_group_name(cls, v: str) -> str:
        return normalize_field(v)


class UserAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()


# =============================================================================
# USER — READ (admin view of a user)
# =============================================================================

class UserAdminRead(BaseModel):
    """Minimal user view for admin actions."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    disabled: bool
    group_id: int | None = None
    group_name: str | None = None


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreateGroupResponse(BaseModel):
    message: str
    group: GroupRead


class UpdateGroupResponse(BaseModel):
    message: str
    group: GroupRead


class DeleteGroupResponse(BaseModel):
    message: str
    group_name: str


class AssignUserGroupResponse(BaseModel):
    message: str
    user: UserAdminRead


class RemoveUserGroupResponse(BaseModel):
    message: str
    user: UserAdminRead


class UserActionResponse(BaseModel):
    message: str
    user: UserAdminRead



"""Admin business logic."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import asc, desc, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.core.slug import generate_slug
from api.core.validators import normalize_field
from api.models.users import Group, User
from api.users.schemas import ReadUser
from api.admin.schemas import (
    GroupCreate,
    GroupListResponse,
    GroupRead,
    GroupUpdate,
    UserAction,
    UserAdminRead,
    UserGroupAssign,
)


logger = logging.getLogger(__name__)


# =============================================================================
# ID / ENTITY NARROWING
# =============================================================================

def group_id_of(group: Group) -> int:
    """Narrow Group.id to int or fail with a clean 500."""
    if group.id is None:
        logger.error("Group row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load group. Please try again.",
        )
    return group.id


def user_id_of(user: User) -> int:
    """Narrow User.id to int or fail with a clean 500."""
    if user.id is None:
        logger.error("User row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load user. Please try again.",
        )
    return user.id


# =============================================================================
# HELPERS
# =============================================================================

async def get_user_by_email(
    db: AsyncSession,
    email: str,
) -> User | None:
    """Fetch user by normalized email."""
    normalized_email = email.strip().lower()
    try:
        result = await db.execute(
            select(User).where(col(User.email) == normalized_email)
        )
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching user email=%s",
            normalized_email,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load user. Please try again.",
        ) from exc


async def get_group_by_slug(
    db: AsyncSession,
    slug: str,
    *,
    must_exist: bool = True,
) -> Group | None:
    """
    Fetch a group by slug.

    If `must_exist` is True (default), raises 404 when missing.
    If False, returns None — used by update/delete paths that
    want to decide their own error shape.
    """
    normalized_slug = slug.strip().lower()
    try:
        result = await db.execute(
            select(Group).where(col(Group.slug) == normalized_slug)
        )
        group = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching group slug=%s",
            normalized_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load group. Please try again.",
        ) from exc

    if group is None and must_exist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{slug}' not found",
        )
    return group


async def get_group_by_name(
    db: AsyncSession,
    name: str,
    *,
    exclude_group_id: int | None = None,
) -> Group | None:
    """Fetch a group by name, optionally excluding a specific id."""
    normalized_name = normalize_field(name)
    try:
        stmt = select(Group).where(col(Group.name) == normalized_name)
        if exclude_group_id is not None:
            stmt = stmt.where(col(Group.id) != exclude_group_id)
        result = await db.execute(stmt)
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching group name=%s",
            normalized_name,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load group. Please try again.",
        ) from exc


async def get_group_by_permission(
    db: AsyncSession,
    permission: str,
    *,
    exclude_group_id: int | None = None,
) -> Group | None:
    """Fetch a group by permission, optionally excluding a specific id."""
    normalized_permission = normalize_field(permission)
    try:
        stmt = select(Group).where(
            col(Group.permission) == normalized_permission,
        )
        if exclude_group_id is not None:
            stmt = stmt.where(col(Group.id) != exclude_group_id)
        result = await db.execute(stmt)
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching group permission=%s",
            normalized_permission,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load group. Please try again.",
        ) from exc


async def get_group_by_slug_excluding_id(
    db: AsyncSession,
    slug: str,
    exclude_group_id: int,
) -> Group | None:
    """Fetch a group by slug, excluding a specific id — for update."""
    normalized_slug = slug.strip().lower()
    try:
        result = await db.execute(
            select(Group).where(
                col(Group.slug) == normalized_slug,
                col(Group.id) != exclude_group_id,
            )
        )
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while checking group slug uniqueness: "
            "slug=%s exclude_id=%s",
            normalized_slug, exclude_group_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate group slug.",
        ) from exc


async def count_users_in_group(
    db: AsyncSession,
    group_id: int,
) -> int:
    """Count users currently assigned to a group."""
    try:
        result = await db.execute(
            select(func.count())
            .select_from(User)
            .where(col(User.group_id) == group_id)
        )
        return result.scalar() or 0
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to count users for group_id=%s", group_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load group. Please try again.",
        ) from exc


# =============================================================================
# BUILDERS — pure, no DB / no HTTP
# =============================================================================

def to_group_read(group: Group) -> GroupRead:
    """Assemble a GroupRead from a Group ORM row."""
    return GroupRead.model_validate(group)


def to_group_list(
    groups: list[Group] | tuple[Group, ...],
    *,
    total: int | None = None,
) -> GroupListResponse:
    """Assemble the group list envelope."""
    items = [to_group_read(g) for g in groups]
    return GroupListResponse(
        total=total if total is not None else len(items),
        groups=items,
    )


def to_user_admin_read(
    user: User,
    *,
    group_name: str | None = None,
) -> UserAdminRead:
    """Assemble a UserAdminRead, optionally with the group's name."""
    read = UserAdminRead.model_validate(user)
    read.group_name = group_name
    return read


# =============================================================================
# GROUP — CREATE
# =============================================================================

async def create_group(
    data: GroupCreate,
    db: AsyncSession,
) -> dict:
    """
    Create a new permission group.

    - `name` and `permission` are unique.
    - Slug is generated from `name`.

    Contracts:
        - Pre-checks for name/permission collisions.
        - flush → commit → refresh.
        - IntegrityError on the race window returns 409.
    """

    # ------------------------------------------------------------------
    # 1. Pre-check name uniqueness
    # ------------------------------------------------------------------
    existing_name = await get_group_by_name(db, data.name)
    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{data.name}' already exists",
        )

    # ------------------------------------------------------------------
    # 2. Pre-check permission uniqueness
    # ------------------------------------------------------------------
    existing_permission = await get_group_by_permission(
        db, data.permission,
    )
    if existing_permission is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Permission '{data.permission}' already assigned "
                f"to group '{existing_permission.name}'"
            ),
        )

    # ------------------------------------------------------------------
    # 3. Prepare the model
    # ------------------------------------------------------------------
    group = Group(
        name=data.name,
        permission=data.permission,
        slug=generate_slug(data.name),
    )

    db.add(group)

    # ------------------------------------------------------------------
    # 4. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after group IntegrityError: name=%s",
                data.name,
            )

        logger.warning(
            "Group creation violated a database integrity constraint: "
            "name=%s, permission=%s, rollback_succeeded=%s",
            data.name, data.permission, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Group '{data.name}' or permission "
                f"'{data.permission}' already exists."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after group flush error: name=%s",
                data.name,
            )

        logger.exception(
            "Database error while flushing new group: "
            "name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create group. "
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
                "Rollback failed after unexpected flush error: name=%s",
                data.name,
            )

        logger.exception(
            "Unexpected error while flushing new group: "
            "name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating group.",
        ) from exc

    # ------------------------------------------------------------------
    # 5. Commit
    # ------------------------------------------------------------------
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
            "Group commit failed or has an uncertain outcome: name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The group creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 6. Refresh (best-effort) + return
    # ------------------------------------------------------------------
    try:
        await db.refresh(group)
    except Exception:
        logger.exception(
            "Failed to refresh group after create: name=%s", data.name,
        )

    logger.info(
        "Group created: id=%s, name=%s, permission=%s",
        group.id, group.name, group.permission,
    )

    return {
        "message": f"Group '{group.name}' created successfully",
        "group": to_group_read(group),
    }


# =============================================================================
# GROUP — READ (single, admin)
# =============================================================================

async def read_single_group(
    db: AsyncSession,
    slug: str,
    current_user: ReadUser,
) -> GroupRead:
    """Fetch a group by slug. Admin-only."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action not allowed",
        )

    group = await get_group_by_slug(db, slug, must_exist=True)
    # get_group_by_slug raises 404 when missing; mypy sees Optional
    assert group is not None
    return to_group_read(group)


# =============================================================================
# GROUP — READ (list, admin)
# =============================================================================

async def list_groups(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> GroupListResponse:
    """Paginated list of groups, alphabetically sorted."""
    try:
        total: int = (
            await db.execute(
                select(func.count()).select_from(Group)
            )
        ).scalar_one()

        result = await db.execute(
            select(Group)
            .order_by(asc(col(Group.name)))
            .offset(skip)
            .limit(limit)
        )
        groups = list(result.scalars().all())

    except SQLAlchemyError as exc:
        logger.exception("Failed to list groups")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load groups. Please try again.",
        ) from exc

    return to_group_list(groups, total=total)


# =============================================================================
# GROUP — UPDATE
# =============================================================================

async def update_group(
    slug: str,
    data: GroupUpdate,
    db: AsyncSession,
) -> dict:
    """
    Update a permission group (name, permission).

    Contract — same as update_firm / update_payment_method:
        - Empty payload rejected.
        - No-op rejected.
        - Name change regenerates slug.
        - Uniqueness pre-checks for name, permission, and slug.
        - flush → commit → refresh.
    """

    # ------------------------------------------------------------------
    # 1. Resolve the group
    # ------------------------------------------------------------------
    group = await get_group_by_slug(db, slug, must_exist=True)
    assert group is not None
    gid = group_id_of(group)

    # ------------------------------------------------------------------
    # 2. Reject empty payload
    # ------------------------------------------------------------------
    if all(
        v is None for v in (data.name, data.permission)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ------------------------------------------------------------------
    # 3. Update only fields that actually changed
    # ------------------------------------------------------------------
    updated_fields: list[str] = []

    # --- name (also regenerates slug) ---
    if data.name is not None and data.name != group.name:
        existing_name = await get_group_by_name(
            db, data.name, exclude_group_id=gid,
        )
        if existing_name is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group name '{data.name}' already exists",
            )

        new_slug = generate_slug(data.name)
        existing_slug = await get_group_by_slug_excluding_id(
            db, new_slug, exclude_group_id=gid,
        )
        if existing_slug is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group slug '{new_slug}' already exists",
            )

        group.name = data.name
        group.slug = new_slug
        updated_fields.extend(["name", "slug"])

    # --- permission ---
    if (
        data.permission is not None
        and data.permission != group.permission
    ):
        existing_permission = await get_group_by_permission(
            db, data.permission, exclude_group_id=gid,
        )
        if existing_permission is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Permission '{data.permission}' is already "
                    f"assigned to group '{existing_permission.name}'"
                ),
            )
        group.permission = data.permission
        updated_fields.append("permission")

    # ------------------------------------------------------------------
    # 4. Reject no-op
    # ------------------------------------------------------------------
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected – all supplied values are "
                "identical to the current values"
            ),
        )

    # ------------------------------------------------------------------
    # 5. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after group IntegrityError: "
                "group_id=%s", gid,
            )

        logger.warning(
            "Group update violated a database integrity constraint: "
            "group_id=%s, rollback_succeeded=%s",
            gid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The update conflicts with an existing group name, "
                "slug, or permission."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after group flush error: group_id=%s",
                gid,
            )

        logger.exception(
            "Database error while flushing group update: "
            "group_id=%s, rollback_succeeded=%s",
            gid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update group. "
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
                "group_id=%s", gid,
            )

        logger.exception(
            "Unexpected error while flushing group update: "
            "group_id=%s, rollback_succeeded=%s",
            gid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating group.",
        ) from exc

    # ------------------------------------------------------------------
    # 6. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: group_id=%s",
                gid,
            )

        logger.exception(
            "Group commit failed or has an uncertain outcome: group_id=%s",
            gid,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The group update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 7. Refresh (best-effort) + return
    # ------------------------------------------------------------------
    try:
        await db.refresh(group)
    except Exception:
        logger.exception(
            "Failed to refresh group after update: group_id=%s", gid,
        )

    logger.info(
        "Group updated successfully: group_id=%s, fields=%s",
        gid, ",".join(updated_fields),
    )

    return {
        "message": f"Group '{group.name}' updated successfully",
        "group": to_group_read(group),
    }


# =============================================================================
# GROUP — DELETE
# =============================================================================

async def delete_group(
    slug: str,
    db: AsyncSession,
) -> dict:
    """
    Delete a permission group.

    Refused if any user is still assigned. Pre-check backs the DB
    FK; IntegrityError covers the concurrent-assignment race.
    """
    group = await get_group_by_slug(db, slug, must_exist=True)
    assert group is not None
    gid = group_id_of(group)
    group_name = group.name

    # ------------------------------------------------------------------
    # 1. Refuse if any user is assigned
    # ------------------------------------------------------------------
    user_count = await count_users_in_group(db, gid)
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete group '{group_name}' — it has "
                f"{user_count} "
                f"{'user' if user_count == 1 else 'users'} assigned. "
                f"Remove all users first."
            ),
        )

    # ------------------------------------------------------------------
    # 2. Stage the delete + flush
    # ------------------------------------------------------------------
    try:
        await db.delete(group)
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after group IntegrityError: "
                "group_id=%s", gid,
            )

        logger.warning(
            "Group delete blocked by FK constraint: group_id=%s, "
            "rollback_succeeded=%s",
            gid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Group '{group_name}' now has users assigned and "
                f"cannot be deleted. Refresh and try again."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after group delete flush error: "
                "group_id=%s", gid,
            )

        logger.exception(
            "Database error while flushing group delete: "
            "group_id=%s, rollback_succeeded=%s",
            gid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to delete group. "
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
                "Rollback failed after unexpected delete flush error: "
                "group_id=%s", gid,
            )

        logger.exception(
            "Unexpected error while flushing group delete: "
            "group_id=%s, rollback_succeeded=%s",
            gid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting group.",
        ) from exc

    # ------------------------------------------------------------------
    # 3. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: group_id=%s",
                gid,
            )

        logger.exception(
            "Group delete commit failed or has an uncertain outcome: "
            "group_id=%s", gid,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The group deletion could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    logger.info(
        "Group '%s' (id=%s) deleted", group_name, gid,
    )

    return {
        "message": f"Group '{group_name}' deleted successfully",
        "group_name": group_name,
    }


# =============================================================================
# USER — ASSIGN / REMOVE GROUP
# =============================================================================

async def assign_user_to_group(
    data: UserGroupAssign,
    db: AsyncSession,
) -> dict:
    """
    Assign a user to a permission group.

    - Both user and group must exist.
    - A user already in the target group returns 400.
    - A user in a *different* group is moved (group_id replaced).
    """
    user = await get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found",
        )
    uid = user_id_of(user)

    group = await get_group_by_name(db, data.group_name)
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{data.group_name}' not found",
        )
    gid = group_id_of(group)

    if user.group_id == gid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"User '{data.email}' is already in group "
                f"'{data.group_name}'"
            ),
        )

    user.group_id = gid

    try:
        await db.flush()
    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after assign-user flush error: "
                "user_id=%s", uid,
            )
        logger.exception(
            "Database error while assigning user to group: "
            "user_id=%s, group_id=%s, rollback_succeeded=%s",
            uid, gid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to assign user to group. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: user_id=%s",
                uid,
            )
        logger.exception(
            "Assign-user commit failed or has an uncertain outcome: "
            "user_id=%s, group_id=%s", uid, gid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The group assignment could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(user)
    except Exception:
        logger.exception(
            "Failed to refresh user after assign: user_id=%s", uid,
        )

    logger.info(
        "User %s assigned to group '%s'", data.email, group.name,
    )

    return {
        "message": (
            f"User '{data.email}' assigned to group '{group.name}'"
        ),
        "user": to_user_admin_read(user, group_name=group.name),
    }


async def remove_user_from_group(
    data: UserAction,
    db: AsyncSession,
) -> dict:
    """Remove a user from their current group."""
    user = await get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found",
        )
    uid = user_id_of(user)

    if user.group_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is not in any group",
        )

    old_group_id = user.group_id
    group = await db.get(Group, old_group_id)
    group_name = group.name if group is not None else "Unknown"

    user.group_id = None

    try:
        await db.flush()
    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after remove-from-group flush error: "
                "user_id=%s", uid,
            )
        logger.exception(
            "Database error while removing user from group: "
            "user_id=%s, rollback_succeeded=%s",
            uid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to remove user from group. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: user_id=%s",
                uid,
            )
        logger.exception(
            "Remove-from-group commit failed or has an uncertain "
            "outcome: user_id=%s", uid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The group removal could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(user)
    except Exception:
        logger.exception(
            "Failed to refresh user after remove-from-group: user_id=%s",
            uid,
        )

    logger.info(
        "User %s removed from group '%s'", data.email, group_name,
    )

    return {
        "message": (
            f"User '{data.email}' removed from group '{group_name}'"
        ),
        "user": to_user_admin_read(user, group_name=None),
    }


# =============================================================================
# USER — DISABLE / ENABLE
# =============================================================================

async def disable_user(
    data: UserAction,
    db: AsyncSession,
) -> dict:
    """Disable a user account."""
    user = await get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found",
        )
    uid = user_id_of(user)

    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is already disabled",
        )

    user.disabled = True

    try:
        await db.flush()
    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after disable-user flush error: "
                "user_id=%s", uid,
            )
        logger.exception(
            "Database error while disabling user: "
            "user_id=%s, rollback_succeeded=%s",
            uid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to disable user. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: user_id=%s",
                uid,
            )
        logger.exception(
            "Disable-user commit failed or has an uncertain outcome: "
            "user_id=%s", uid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The user disable action could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(user)
    except Exception:
        logger.exception(
            "Failed to refresh user after disable: user_id=%s", uid,
        )

    logger.info("User %s disabled", data.email)

    return {
        "message": f"User '{data.email}' has been disabled",
        "user": to_user_admin_read(user),
    }


async def enable_user(
    data: UserAction,
    db: AsyncSession,
) -> dict:
    """Re-enable a disabled user account."""
    user = await get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.email}' not found",
        )
    uid = user_id_of(user)

    if not user.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is already active",
        )

    user.disabled = False

    try:
        await db.flush()
    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after enable-user flush error: "
                "user_id=%s", uid,
            )
        logger.exception(
            "Database error while enabling user: "
            "user_id=%s, rollback_succeeded=%s",
            uid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to enable user. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: user_id=%s",
                uid,
            )
        logger.exception(
            "Enable-user commit failed or has an uncertain outcome: "
            "user_id=%s", uid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The user enable action could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(user)
    except Exception:
        logger.exception(
            "Failed to refresh user after enable: user_id=%s", uid,
        )

    logger.info("User %s re-activated", data.email)

    return {
        "message": f"User '{data.email}' has been re-activated",
        "user": to_user_admin_read(user),
}



"""Admin routes."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from api.deps import DBDep, AdminUser
from api.admin.logics import (
    assign_user_to_group,
    create_group,
    delete_group,
    disable_user,
    enable_user,
    list_groups,
    read_single_group,
    remove_user_from_group,
    update_group,
)
from api.admin.schemas import (
    AssignUserGroupResponse,
    CreateGroupResponse,
    DeleteGroupResponse,
    GroupCreate,
    GroupListResponse,
    GroupRead,
    GroupUpdate,
    RemoveUserGroupResponse,
    UserAction,
    UserActionResponse,
    UserGroupAssign,
)


router = APIRouter(prefix="/admin", tags=["Admin"])


# =============================================================================
# GROUP — CREATE
# =============================================================================

@router.post(
    "/groups/add",
    response_model=CreateGroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a permission group (admin)",
)
async def create(
    data: GroupCreate,
    db: DBDep,
    current_user: AdminUser,
):
    return await create_group(data=data, db=db)


# =============================================================================
# GROUP — READ (single)
# =============================================================================

@router.get(
    "/groups/{slug}",
    response_model=GroupRead,
    summary="Get a group by slug (admin)",
)
async def read_single(
    slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await read_single_group(
        db=db, slug=slug, current_user=current_user,
    )


# =============================================================================
# GROUP — READ (list)
# =============================================================================

@router.get(
    "/groups",
    response_model=GroupListResponse,
    summary="List groups (admin)",
)
async def list_all(
    db: DBDep,
    current_user: AdminUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    return await list_groups(db=db, skip=skip, limit=limit)


# =============================================================================
# GROUP — UPDATE
# =============================================================================

@router.patch(
    "/groups/{slug}",
    response_model=GroupUpdate and CreateGroupResponse,  # see note below
    summary="Update a group (admin)",
)
async def update(
    slug: str,
    data: GroupUpdate,
    db: DBDep,
    current_user: AdminUser,
):
    return await update_group(slug=slug, data=data, db=db)


# =============================================================================
# GROUP — DELETE
# =============================================================================

@router.delete(
    "/groups/{slug}",
    response_model=DeleteGroupResponse,
    summary="Delete a group (admin)",
)
async def remove(
    slug: str,
    db: DBDep,
    current_user: AdminUser,
):
    return await delete_group(slug=slug, db=db)


# =============================================================================
# USER — ASSIGN GROUP
# =============================================================================

@router.post(
    "/users/assign-group",
    response_model=AssignUserGroupResponse,
    summary="Assign a user to a group (admin)",
)
async def assign_group(
    data: UserGroupAssign,
    db: DBDep,
    current_user: AdminUser,
):
    return await assign_user_to_group(data=data, db=db)


# =============================================================================
# USER — REMOVE GROUP
# =============================================================================

@router.post(
    "/users/remove-group",
    response_model=RemoveUserGroupResponse,
    summary="Remove a user from their group (admin)",
)
async def remove_group(
    data: UserAction,
    db: DBDep,
    current_user: AdminUser,
):
    return await remove_user_from_group(data=data, db=db)


# =============================================================================
# USER — DISABLE / ENABLE
# =============================================================================

@router.post(
    "/users/disable",
    response_model=UserActionResponse,
    summary="Disable a user (admin)",
)
async def disable(
    data: UserAction,
    db: DBDep,
    current_user: AdminUser,
):
    return await disable_user(data=data, db=db)


@router.post(
    "/users/enable",
    response_model=UserActionResponse,
    summary="Re-enable a user (admin)",
)
async def enable(
    data: UserAction,
    db: DBDep,
    current_user: AdminUser,
):
    return await enable_user(data=data, db=db)




