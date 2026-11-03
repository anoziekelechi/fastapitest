
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


vs




async def update_service(
    slug: str,
    data: ServiceUpdate,
    db: AsyncSession,
    service_image: UploadFile | None = None,
) -> dict:
    """
    Update a subscription service.

    - Rejects an empty payload.
    - Updates only fields that actually changed.
    - Uploads a replacement image before touching the DB record.
    - Deletes the new upload after a failed flush only if rollback
      succeeds.
    - Preserves the new upload if commit raises, because its outcome
      may be uncertain.
    - Deletes the old image only after a confirmed successful commit.

    No IntegrityError handling is needed — this endpoint cannot violate
    a unique constraint (name and slug are not editable here).
    """

    # ---------------------------------------------------------
    # 1. Retrieve the existing service
    # ---------------------------------------------------------
    service = await get_service_by_slug(db=db, slug=slug)

    # ==============================================================
    # 2. Reject completely empty update payload
    # ==============================================================
    if all(
        value is None
        for value in (
            data.description,
            data.trial_days,
            service_image,
            data.is_active,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ---------------------------------------------------------
    # 3. Preserve the old image key
    # ---------------------------------------------------------
    old_image_key = service.service_logo_key
    new_image_key: str | None = None

    # ---------------------------------------------------------
    # 4. Upload a replacement image, if supplied
    # ---------------------------------------------------------
    if service_image is not None:
        try:
            new_image_key = await handle_file_update(
                file=service_image,
                current_key=None,
                prefix=SERVICE_IMAGE,
                max_size=SERVICE_IMAGE_MAX_SIZE,
            )

            if not new_image_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the service image.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Service image upload failed: service_id=%s",
                service.id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload service image.",
            ) from exc

    # ==============================================================
    # 5. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    if data.description is not None:
        if data.description != service.description:
            service.description = data.description
            updated_fields.append("description")

    if data.trial_days is not None:
        if data.trial_days != service.trial_days:
            service.trial_days = data.trial_days
            updated_fields.append("trial_days")

    if data.is_active is not None:
        if data.is_active != service.is_active:
            service.is_active = data.is_active
            updated_fields.append("is_active")

    if new_image_key is not None:
        if new_image_key != service.service_logo_key:
            service.service_logo_key = new_image_key
            updated_fields.append("service_logo_key")

    # ==============================================================
    # 6. Reject if nothing actually changed
    # ==============================================================
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ---------------------------------------------------------
    # 7. Flush before attempting commit
    #
    # No IntegrityError branch — this update cannot violate a
    # unique constraint. SQLAlchemyError and Exception cover
    # the realistic failure modes.
    # ---------------------------------------------------------
    try:
        await db.flush()

    except SQLAlchemyError as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after service flush error: "
                "service_id=%s",
                service.id,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "failure: service_id=%s, image_key=%s",
                    service.id,
                    new_image_key,
                )

        logger.exception(
            "Database error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            service.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update service. "
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
                "service_id=%s",
                service.id,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "failure: service_id=%s, image_key=%s",
                    service.id,
                    new_image_key,
                )

        logger.exception(
            "Unexpected error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            service.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating service.",
        ) from exc

    # ---------------------------------------------------------
    # 8. Commit the transaction
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # IMPORTANT:
        # Do not delete new_image_key here.
        #
        # A commit exception does not prove the database rejected
        # the transaction. The commit may have succeeded while its
        # acknowledgement was lost.
        #
        # Preserve the uploaded image until the database state can
        # be verified and any orphaned object reconciled.

        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "service_id=%s",
                service.id,
            )

        logger.exception(
            "Service commit failed or has an uncertain outcome. "
            "Preserving replacement image for reconciliation: "
            "service_id=%s, new_image_key=%s",
            service.id,
            new_image_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 9. Commit succeeded: refresh and delete the old image
    # ---------------------------------------------------------
    try:
        await db.refresh(service)
    except Exception:
        # Refresh failure after commit is not fatal — the update
        # is already persisted. Log and continue.
        logger.exception(
            "Failed to refresh service after update: service_id=%s",
            service.id,
        )

    if (
        new_image_key is not None
        and old_image_key is not None
        and old_image_key != new_image_key
    ):
        try:
            delete_from_r2(old_image_key)

        except Exception:
            # The database update has already succeeded.
            # Do not report the update as failed because old-image
            # cleanup failed; log it for later reconciliation.
            logger.exception(
                "Service updated, but the old image could not be "
                "deleted: service_id=%s, old_image_key=%s",
                service.id,
                old_image_key,
            )

    # ---------------------------------------------------------
    # 10. Return the updated service
    # ---------------------------------------------------------
    logger.info(
        "Service updated successfully: service_id=%s, fields=%s",
        service.id,
        ",".join(updated_fields),
    )

    return {
        "message": "Service updated successfully.",
        "service": to_service_read(service),
        }




# can you explain why one uses this and otjers not using this block
# ==============================================================
    # 2. Reject completely empty update payload
    # ==============================================================
    if all(
        value is None
        for value in (
            data.description,
            data.trial_days,
            service_image,
            data.is_active,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )




  # 6. Reject if nothing actually changed
    # ==============================================================
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

