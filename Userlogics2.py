from fastapi import HTTPException, UploadFile, status
from sqlalchemy import col, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


# =============================================================================
# CREATE PAYMENT METHOD — ADMIN ONLY
# =============================================================================

async def create_payment_method(
    data: PaymentMethodCreate,
    payment_logo: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a payment method.

    Admin access is enforced by the route's AdminUser dependency.

    The payment method name is globally unique.
    The logo is optional and stored in Cloudflare R2.

    A failed database transaction triggers a rollback and a best-effort
    cleanup of the newly uploaded logo.
    """

    # ------------------------------------------------------------------
    # 1. Check for an existing payment method name
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(PaymentMethods).where(
                col(PaymentMethods.name) == data.name,
            
        )
        existing = result.scalars().first()

    except SQLAlchemyError:
        await db.rollback()

        logger.exception(
            "Database error checking payment method name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment method '{data.name}' already exists.",
        )

    # ------------------------------------------------------------------
    # 2. Upload the optional logo
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
    # 3. Prepare the model
    # ------------------------------------------------------------------
    method = PaymentMethods(
        name=data.name,
        slug=generate_slug(data.name),
        payment_logo_key=new_logo_key,
    )

    # ------------------------------------------------------------------
    # 4. Best-effort cleanup for a failed database transaction
    # ------------------------------------------------------------------
    def cleanup_new_logo() -> None:
        if new_logo_key is None:
            return

        try:
            delete_from_r2(new_logo_key)

        except Exception:
            logger.exception(
                "Failed to clean up payment logo in R2: key=%s",
                new_logo_key,
            )

    # ------------------------------------------------------------------
    # 5. Persist the payment method
    # ------------------------------------------------------------------
    try:
        db.add(method)

        # Execute the INSERT and surface database constraints before commit.
        await db.flush()

        # Load database-generated values while the transaction is active.
        await db.refresh(method)

        # Commit only after the preceding operations succeed.
        await db.commit()

    except IntegrityError:
        await db.rollback()

        logger.warning(
            "Payment method creation violated a database integrity "
            "constraint: name=%s",
            data.name,
        )

        cleanup_new_logo()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Payment method '{data.name}' already exists "
                "or conflicts with a database constraint."
            ),
        )

    except SQLAlchemyError:
        await db.rollback()

        logger.exception(
            "Database error creating payment method name=%s",
            data.name,
        )

        cleanup_new_logo()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    except Exception:
        # Covers unexpected failures during persistence or refresh.
        # The transaction has not been committed if this block is reached.
        await db.rollback()

        logger.exception(
            "Unexpected error creating payment method name=%s",
            data.name,
        )

        cleanup_new_logo()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    # ------------------------------------------------------------------
    # 6. Log successful creation
    # ------------------------------------------------------------------
    logger.info(
        "Payment method '%s' (slug=%s) created by admin user_id=%s",
        method.name,
        method.slug,
        current_user.id,
    )

    # ------------------------------------------------------------------
    # 7. Return the response
    # ------------------------------------------------------------------
    return {
        "message": (
            f"Payment method '{method.name}' created successfully."
        ),
        "payment_method": to_payment_method_read(method),
            }


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
