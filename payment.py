
# =============================================================================
# READ — AVAILABLE
# PUBLIC — ACTIVE PAYMENT METHODS ONLY
# =============================================================================

async def read_available_payment_methods(
    db: AsyncSession,
) -> AvailablePaymentMethodsRead:
    """
    Return active payment methods for the public frontend.

    Used by the public 'We accept' widget.

    Inactive methods remain in the database and are visible to admins,
    but are not returned to public customers.
    """

    try:
        result = await db.execute(
            select(PaymentMethods)
            .where(
                col(PaymentMethods.is_active).is_(True)
            )
            .order_by(
                col(PaymentMethods.name)
            )
        )

        methods = result.scalars().all()

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load available payment methods"
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        ) from exc

    return AvailablePaymentMethodsRead(
        total=len(methods),
        methods=[
            to_payment_method_read(method)
            for method in methods
        ],
    )


# =============================================================================
# READ — ALL
# ADMIN ONLY — ACTIVE AND INACTIVE PAYMENT METHODS
# =============================================================================

async def read_all_payment_methods(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> AvailablePaymentMethodsRead:
    """
    Return all payment methods, including active and inactive methods.

    Admin access is enforced by the route's AdminUser dependency.
    """

    try:
        total: int = (
            await db.execute(
                select(func.count()).select_from(
                    PaymentMethods
                )
            )
        ).scalar_one()

        result = await db.execute(
            select(PaymentMethods)
            .order_by(
                col(PaymentMethods.name)
            )
            .offset(skip)
            .limit(limit)
        )

        methods = result.scalars().all()

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to list all payment methods"
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        ) from exc

    return AvailablePaymentMethodsRead(
        total=total,
        methods=[
            to_payment_method_read(method)
            for method in methods
        ],
    )


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
    Update a payment method.

    Supported updates:
        - name
        - provider_code
        - is_active
        - payment logo

    Behavior:
        - Validates and normalizes the name through PaymentMethodUpdate.
        - Checks for duplicate payment method names.
        - Regenerates the slug only when the name changes.
        - Uploads a replacement logo before changing the database record.
        - Deletes a newly uploaded logo after a failed flush only when
          rollback succeeds.
        - Preserves the new logo if commit outcome is uncertain.
        - Deletes the old logo only after confirmed successful commit.

    Admin access is enforced by the route's AdminUser dependency.
    """

    new_logo_key: str | None = None
    old_logo_key: str | None = None

    # ---------------------------------------------------------
    # 1. Retrieve the existing payment method
    # ---------------------------------------------------------
    method = await get_payment_by_slug(
        db=db,
        slug=slug,
    )

    if method is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found.",
        )

    # ---------------------------------------------------------
    # 2. Determine which fields were supplied
    # ---------------------------------------------------------
    changes = data.model_dump(exclude_unset=True)

    new_name = changes.get("name")
    new_provider_code = changes.get("provider_code")
    new_is_active = changes.get("is_active")

    name_changed = (
        new_name is not None
        and new_name != method.name
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

    # ---------------------------------------------------------
    # 3. Check whether the new name already exists
    # ---------------------------------------------------------
    if name_changed and new_name is not None:
        try:
            existing_id = await db.scalar(
                select(PaymentMethods.id).where(
                    col(PaymentMethods.name) == new_name,
                    col(PaymentMethods.id) != method.id,
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

    if provider_code_changed and new_provider_code is not None:
        method.provider_code = new_provider_code

    if is_active_changed and new_is_active is not None:
        method.is_active = new_is_active

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
        # A commit exception does not prove that the database rejected
        # the transaction. Preserve the new logo for reconciliation.

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
        "Payment method updated successfully: id=%s, "
        "provider_code=%s, is_active=%s",
        method.id,
        method.provider_code,
        method.is_active,
    )

    return {
        "message": "Payment method updated successfully.",
        "payment_method": to_payment_method_read(method),
    }








# ---------------------------------------------------------
# Check whether the new provider code already exists
# ---------------------------------------------------------
if provider_code_changed and new_provider_code is not None:
    try:
        existing_provider_id = await db.scalar(
            select(PaymentMethods.id).where(
                col(PaymentMethods.provider_code) == new_provider_code,
                col(PaymentMethods.id) != method.id,
            )
        )

    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after duplicate-provider query error: "
                "payment_method_id=%s",
                method.id,
            )

        logger.exception(
            "Failed to check payment provider code uniqueness: "
            "payment_method_id=%s",
            method.id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate payment provider code.",
        ) from exc

    if existing_provider_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Payment provider code '{new_provider_code}' "
                "already exists."
            ),
        )




