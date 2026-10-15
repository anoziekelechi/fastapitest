
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
