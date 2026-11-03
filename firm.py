# =============================================================================
# UPDATE (admin)
# =============================================================================

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
        - Empty payload rejected (nothing supplied).
        - No-op rejected (something supplied, but nothing changed).
        - Upload replacement logo before touching the DB.
        - On flush failure, delete the new logo only if rollback
          succeeded.
        - On commit failure, preserve the new logo (uncertain
          outcome).
        - Delete the old logo only after confirmed commit.

    IntegrityError branch is required: `name` is globally unique, so
    a concurrent create can collide with the rename between our
    pre-check and the DB write.
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

    # ==============================================================
    # 2. Reject completely empty update payload
    # ==============================================================
    if all(
        value is None
        for value in (data.name, data.is_active, payment_logo)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ------------------------------------------------------------------
    # 3. Pre-check name uniqueness
    #
    # Only when the name is actually changing. Sending the same name
    # back is a no-op, which step 6 will reject — no need to hit the
    # DB for a uniqueness check we know is going to pass.
    # ------------------------------------------------------------------
    name_changed = (
        data.name is not None and data.name != method.name
    )

    if name_changed and data.name is not None:
        existing = await get_payment_by_name(
            db, data.name, exclude_payment_id=mid,
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
    new_logo_key: str | None = None

    # ------------------------------------------------------------------
    # 5. Upload replacement logo, if supplied
    # ------------------------------------------------------------------
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

    # ==============================================================
    # 6. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    # --- name (also regenerates slug) ---
    if name_changed and data.name is not None:
        method.name = data.name
        method.slug = generate_slug(data.name)
        updated_fields.append("name")

    # --- is_active ---
    if (
        data.is_active is not None
        and data.is_active != method.is_active
    ):
        method.is_active = data.is_active
        updated_fields.append("is_active")

    # --- logo ---
    if new_logo_key is not None and new_logo_key != old_logo_key:
        method.payment_logo_key = new_logo_key
        updated_fields.append("payment_logo_key")

    # ==============================================================
    # 7. Reject if nothing actually changed
    #
    # Catches the case where the caller supplied one or more fields
    # but every supplied value is identical to the current value.
    # Without this, an "update" that changes nothing would still
    # trigger a commit and, more importantly, could quietly consume
    # a fresh logo upload (new_logo_key above) for no reason.
    # ==============================================================
    if not updated_fields:
        # Clean up an uploaded-but-unused logo so we don't leak it.
        if new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up unused logo after no-op "
                    "update: payment_method_id=%s, logo_key=%s",
                    mid, new_logo_key,
                )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ---------------------------------------------------------
    # 8. Flush before attempting commit
    #
    # IntegrityError is possible: `name` is unique, and a
    # concurrent create can claim the new name between our
    # pre-check (step 3) and this flush.
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

    # ---------------------------------------------------------
    # 9. Commit
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # 10. Refresh (best-effort)
    # ---------------------------------------------------------
    try:
        await db.refresh(method)
    except Exception:
        logger.exception(
            "Failed to refresh payment method after update: id=%s", mid,
        )

    # ---------------------------------------------------------
    # 11. Delete old logo after confirmed commit
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
                mid, old_logo_key,
            )

    # ---------------------------------------------------------
    # 12. Return via builder
    # ---------------------------------------------------------
    logger.info(
        "Payment method updated successfully: id=%s, fields=%s, "
        "provider_code=%s, is_active=%s",
        mid,
        ",".join(updated_fields),
        method.provider_code,
        method.is_active,
    )

    return {
        "message": "Payment method updated successfully.",
        "payment_method": to_payment_method_read(method),
    }
