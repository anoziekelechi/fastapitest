
import logging

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import col, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def update_payment_method(
    data: PaymentMethodUpdate,
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
    payment_logo: UploadFile | None = None,
) -> dict:
    """
    Update an existing payment method.

    Rules:
        - Pydantic validates and normalizes the payment name.
        - Payment names must be unique.
        - The slug changes only when the name changes.
        - A replacement logo is uploaded before database persistence.
        - The old logo is deleted only after a successful commit.
        - A newly uploaded logo is cleaned up after a confirmed
          database failure.
        - If the commit outcome is uncertain, preserve the new logo
          for reconciliation.
    """

    new_logo_key: str | None = None

    # ================================================================
    # 1. Retrieve the existing payment method
    # ================================================================

    try:
        method = await get_payment_by_slug(
            db=db,
            slug=slug,
        )

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        await db.rollback()

        logger.exception(
            "Database error retrieving payment method: slug=%s",
            slug,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve payment method.",
        ) from exc

    if method is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found.",
        )

    # ================================================================
    # 2. Determine whether the name changes
    # ================================================================

    new_name = data.name

    name_changed = (
        new_name is not None
        and new_name != method.name
    )

    # ================================================================
    # 3. Reject requests without actual changes
    # ================================================================

    if not name_changed and payment_logo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes detected.",
        )

    # ================================================================
    # 4. Check name uniqueness if the name changes
    # ================================================================

    if name_changed:
        # name_changed implies new_name is not None at runtime.
        # Use an explicit guard so Pylance narrows the type safely.
        if new_name is not None:
            try:
                result = await db.execute(
                    select(PaymentMethods.id).where(
                        col(PaymentMethods.name) == new_name,
                        col(PaymentMethods.id) != method.id,
                    )
                )

                existing_id = result.scalar_one_or_none()

            except SQLAlchemyError as exc:
                await db.rollback()

                logger.exception(
                    "Database error checking payment-name uniqueness: "
                    "payment_method_id=%s",
                    method.id,
                )

                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to validate payment method update.",
                ) from exc

            if existing_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "A payment method with this name already exists."
                    ),
                )

    # ================================================================
    # 5. Preserve existing values
    # ================================================================

    old_name = method.name
    old_slug = method.slug
    old_logo_key = method.payment_logo_key

    updated_fields: list[str] = []

    # ================================================================
    # 6. Upload replacement logo, if provided
    # ================================================================

    if payment_logo is not None:
        try:
            new_logo_key = await handle_file_update(
                file=payment_logo,
                current_key=None,
                prefix=PAYMENT_LOGO,
                max_size=PAYMENT_LOGO_MAX_SIZE,
            )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Unexpected error uploading payment logo: "
                "payment_method_id=%s",
                method.id,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload payment logo.",
            ) from exc

        if not new_logo_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to obtain the uploaded payment logo key.",
            )

    # ================================================================
    # 7. Apply changes to the model
    # ================================================================

    if name_changed and new_name is not None:
        # Pylance knows new_name is str inside this block.
        method.name = new_name
        method.slug = generate_slug(new_name)

        updated_fields.extend(["name", "slug"])

    if new_logo_key is not None:
        method.payment_logo_key = new_logo_key

        updated_fields.append("payment_logo_key")

    # ================================================================
    # 8. Flush changes before committing
    # ================================================================

    try:
        await db.flush()

    except IntegrityError as exc:
        await db.rollback()

        logger.warning(
            "Integrity constraint prevented payment method update: "
            "payment_method_id=%s",
            method.id,
            exc_info=True,
        )

        # The flush failed before commit.
        if new_logo_key is not None:
            delete_from_r2(new_logo_key)

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The payment method update conflicts with "
                "an existing or required database value."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        await db.rollback()

        logger.exception(
            "Database flush failed while updating payment method: "
            "payment_method_id=%s",
            method.id,
        )

        if new_logo_key is not None:
            delete_from_r2(new_logo_key)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save payment method changes.",
        ) from exc

    except Exception as exc:
        await db.rollback()

        logger.exception(
            "Unexpected error before payment method commit: "
            "payment_method_id=%s",
            method.id,
        )

        if new_logo_key is not None:
            delete_from_r2(new_logo_key)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save payment method changes.",
        ) from exc

    # ================================================================
    # 9. Commit database transaction
    # ================================================================

    try:
        await db.commit()

    except IntegrityError as exc:
        # An integrity violation normally means the transaction
        # was rejected by the database.
        try:
            await db.rollback()
        except SQLAlchemyError:
            logger.exception(
                "Rollback failed after payment method integrity error: "
                "payment_method_id=%s",
                method.id,
            )

        logger.warning(
            "Payment method commit failed due to an integrity "
            "constraint: payment_method_id=%s",
            method.id,
            exc_info=True,
        )

        if new_logo_key is not None:
            delete_from_r2(new_logo_key)

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The payment method update conflicts with "
                "an existing or required database value."
            ),
        ) from exc

    except Exception as exc:
        # The commit may have succeeded even if the connection
        # failed before the application received confirmation.
        # Preserve the new logo because the database might reference it.
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after uncertain payment method commit: "
                "payment_method_id=%s",
                method.id,
            )

        logger.exception(
            "Payment method commit outcome is uncertain. "
            "Preserving new logo for reconciliation: "
            "payment_method_id=%s, new_logo_key=%s",
            method.id,
            new_logo_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The payment method update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ================================================================
    # 10. Delete old logo after successful commit
    # ================================================================

    if (
        new_logo_key is not None
        and old_logo_key is not None
        and old_logo_key != new_logo_key
    ):
        # delete_from_r2 logs failures without raising.
        # A cleanup failure does not undo the database update.
        delete_from_r2(old_logo_key)

    # ================================================================
    # 11. Log successful update
    # ================================================================

    logger.info(
        "Payment method updated successfully: "
        "payment_method_id=%s, user_id=%s, fields=%s, "
        "old_name=%s, new_name=%s, old_slug=%s, new_slug=%s",
        method.id,
        current_user.id,
        updated_fields,
        old_name,
        method.name,
        old_slug,
        method.slug,
    )

    # ================================================================
    # 12. Return updated payment method
    # ================================================================

    return {
        "message": "Payment method updated successfully.",
        "payment_method": to_payment_method_read(method),
    }
