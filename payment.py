

# =============================================================================
# CREATE — ADMIN ONLY
# add the two fields we just added in db is_active and provider_code
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
# READ — available (public, deduplicated — for the Footer widget)
# returns only actve payment methods
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
# READ — all (admin only)
# modify it to returns active and non active to admins
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







# include the is_active and provider_code in this update
async def update_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    db: AsyncSession,
    payment_logo: UploadFile | None = None,
) -> dict:
    """
    Update a payment method's name and/or logo.

    - Validates and normalizes the name through PaymentMethodUpdate.
    - Checks for duplicate payment method names.
    - Regenerates the slug only when the name changes.
    - Uploads a replacement logo before changing the database record.
    - Deletes the new upload after a failed flush only if rollback succeeds.
    - Preserves the new upload if commit raises, because its outcome
      may be uncertain.
    - Deletes the old logo only after a confirmed successful commit.
    """

    new_logo_key: str | None = None
    old_logo_key: str | None = None

    # ---------------------------------------------------------
    # 1. Retrieve the existing payment method
    # ---------------------------------------------------------
    # Your getter returns None when no record exists and raises
    # HTTPException(500) when its database operation fails.
    # Preserve those behaviors.
    method = await get_payment_by_slug(db=db, slug=slug)

    if method is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found.",
        )

    # ---------------------------------------------------------
    # 2. Determine which fields actually need updating
    # ---------------------------------------------------------
    new_name = data.name

    name_changed = (
        new_name is not None
        and new_name != method.name
    )

    logo_supplied = payment_logo is not None

    if not name_changed and not logo_supplied:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were provided.",
        )

    # ---------------------------------------------------------
    # 3. Check whether the new name already exists
    # ---------------------------------------------------------
    if name_changed and new_name is not None:
        try:
            existing_id = await db.scalar(
                select(PaymentMethods.id).where(
                    PaymentMethods.name == new_name,
                    PaymentMethods.id != method.id,
                )
            )

        except SQLAlchemyError as exc:
            try:
                await db.rollback()
            except Exception:
                logger.exception(
                    "Rollback failed after duplicate-name query error: "
                    "payment_method_id=%s",
                    method.id,
                )

            logger.exception(
                "Failed to check payment method name uniqueness: "
                "payment_method_id=%s",
                method.id,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to validate payment method name.",
            ) from exc

        if existing_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A payment method with this name already exists.",
            )

    # ---------------------------------------------------------
    # 4. Preserve the old logo key
    # ---------------------------------------------------------
    old_logo_key = method.payment_logo_key

    # ---------------------------------------------------------
    # 5. Upload a replacement logo, if supplied
    # ---------------------------------------------------------
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
                "Payment logo upload failed: payment_method_id=%s",
                method.id,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload payment method logo.",
            ) from exc

    # ---------------------------------------------------------
    # 6. Apply the changes to the ORM object
    # ---------------------------------------------------------
    if name_changed and new_name is not None:
        method.name = new_name
        method.slug = generate_slug(new_name)

    if new_logo_key is not None:
        method.payment_logo_key = new_logo_key

    # ---------------------------------------------------------
    # 7. Flush before attempting commit
    # ---------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method flush IntegrityError: "
                "payment_method_id=%s",
                method.id,
            )

        # Only clean up when rollback has succeeded.
        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush failure: "
                    "payment_method_id=%s, logo_key=%s",
                    method.id,
                    new_logo_key,
                )

        logger.warning(
            "Integrity error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            method.id,
            rollback_succeeded,
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
                "payment_method_id=%s",
                method.id,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush failure: "
                    "payment_method_id=%s, logo_key=%s",
                    method.id,
                    new_logo_key,
                )

        logger.exception(
            "Database error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            method.id,
            rollback_succeeded,
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
                "payment_method_id=%s",
                method.id,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush failure: "
                    "payment_method_id=%s, logo_key=%s",
                    method.id,
                    new_logo_key,
                )

        logger.exception(
            "Unexpected error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            method.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating payment method.",
        ) from exc

    # ---------------------------------------------------------
    # 8. Commit the transaction
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # IMPORTANT:
        # Do not delete new_logo_key here.
        #
        # A commit exception does not prove the database rejected
        # the transaction. The commit may have succeeded while its
        # acknowledgement was lost.
        #
        # Preserve the uploaded logo until the database state can
        # be verified and any orphaned object reconciled.

        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "payment_method_id=%s",
                method.id,
            )

        logger.exception(
            "Payment method commit failed or has an uncertain outcome. "
            "Preserving replacement logo for reconciliation: "
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

    # ---------------------------------------------------------
    # 9. Commit succeeded: delete the old logo
    # ---------------------------------------------------------
    if (
        new_logo_key is not None
        and old_logo_key is not None
        and old_logo_key != new_logo_key
    ):
        try:
            delete_from_r2(old_logo_key)

        except Exception:
            # The database update has already succeeded.
            # Do not report the update as failed because old-logo
            # cleanup failed; log it for later reconciliation.
            logger.exception(
                "Payment method updated, but the old logo could not "
                "be deleted: payment_method_id=%s, old_logo_key=%s",
                method.id,
                old_logo_key,
            )

    # ---------------------------------------------------------
    # 10. Return the updated payment method
    # ---------------------------------------------------------
    logger.info(
        "Payment method updated successfully: payment_method_id=%s",
        method.id,
    )

    return {
        "message": "Payment method updated successfully.",
        "payment_method": to_payment_method_read(method),
            }
