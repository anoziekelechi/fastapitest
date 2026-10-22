
async def update_service(
    slug: str,
    data: ServiceUpdate,
    #current_user: ReadUser,
    db: AsyncSession,
    service_image: UploadFile | None = None,
) -> dict:
    """
    Update a service method's name and/or logo.

    - Validates and normalizes the name through PaymentMethodUpdate.
    - Checks for duplicate payment method names.
    - Regenerates the slug only when the name changes.
    - Uploads a replacement logo before changing the database record.
    - Deletes the new upload after a failed flush only if rollback succeeds.
    - Preserves the new upload if commit raises, because its outcome
      may be uncertain.
    - Deletes the old logo only after a confirmed successful commit.
    """

    new_image_key: str | None = None


    # ---------------------------------------------------------
    # 1. Retrieve the existing subscription service
    # get_service_by_slug handles not found
    # ---------------------------------------------------------
    service = await get_service_by_slug(db=db, slug=slug)
    
    # ==============================================================
    # 1. Reject completely empty update payload  Determine which fields actually 
    # need updating
    # ==============================================================

    if all(
        value is None
        for value in (
            data.description,
            data.trial_days,
            service_image,
            data.is_active
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )
    

    # ---------------------------------------------------------
    # 4. Preserve the old logo key
    # ---------------------------------------------------------
    old_logo_key = service.service_logo_key
    
     # ---------------------------------------------------------
    # 5. Upload a replacement logo, if supplied
    # ---------------------------------------------------------
    if service_image is not None:
        try:
            new_logo_key = await handle_file_update(
                file=service_image,
                current_key=None,
                prefix=SERVICE_IMAGE,
                max_size=SERVICE_IMAGE_MAX_SIZE,
            )

            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the service logo.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "service logo upload failed: service_method_id=%s",
                service.id,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload service method logo.",
            ) from exc
    
    
     # ==============================================================
    # 3. Update only fields that actually changed
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
            
    if service_image is not None:
        if new_logo_key!= service.service_logo_key:
            service.service_logo_key = new_logo_key
            updated_fields.append("service_logo_key")
    

    # ==============================================================
    # 4. Reject if nothing actually changed
    # ==============================================================

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

# since am not checking for unique seem i dont need Integrity error check
# where is this variable comes from rollback_succeeded 
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
                "Rollback failed after service method flush IntegrityError: "
                "payment_method_id=%s",
                service.id,
            )

        # Only clean up when rollback has succeeded.
        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush failure: "
                    "payment_method_id=%s, logo_key=%s",
                    service.id,
                    new_logo_key,
                )

        logger.warning(
            "Integrity error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            service.id,
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
                service.id,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush failure: "
                    "payment_method_id=%s, logo_key=%s",
                    service.id,
                    new_logo_key,
                )

        logger.exception(
            "Database error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            service.id,
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
                service.id,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush failure: "
                    "payment_method_id=%s, logo_key=%s",
                    service.id,
                    new_logo_key,
                )

        logger.exception(
            "Unexpected error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            service.id,
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
                service.id,
            )

        logger.exception(
            "Payment method commit failed or has an uncertain outcome. "
            "Preserving replacement logo for reconciliation: "
            "payment_method_id=%s, new_logo_key=%s",
            service.id,
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
                service.id,
                old_logo_key,
            )

    # ---------------------------------------------------------
    # 10. Return the updated payment method
    # ---------------------------------------------------------
    logger.info(
        "Payment method updated successfully: payment_method_id=%s",
        service.id,
    )

    return {
        "message": "Payment method updated successfully.",
        "payment_method": to_service_read(service),
            }




