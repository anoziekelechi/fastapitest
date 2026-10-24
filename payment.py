

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class PaymentMethodUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    provider_code: str | None = None
    is_active: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str | None:
        if value is None:
            return None

        return normalize_payment_option(
            value,
            field_name="name",
        )

    @field_validator("provider_code", mode="before")
    @classmethod
    def validate_provider_code(
        cls,
        value: object,
    ) -> str | None:
        if value is None:
            return None

        if not isinstance(value, str):
            raise ValueError("Provider code must be a string.")

        normalized = value.strip().lower()

        if not normalized:
            raise ValueError("Provider code cannot be empty.")

        # Replace this example with your actual supported provider codes.
        allowed_codes = {"momo", "orange"}

        if normalized not in allowed_codes:
            raise ValueError("Unsupported payment provider code.")

        return normalized

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> "PaymentMethodUpdate":
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Payment method name cannot be null.")

        if (
            "provider_code" in self.model_fields_set
            and self.provider_code is None
        ):
            raise ValueError("Payment provider code cannot be null.")

        if (
            "is_active" in self.model_fields_set
            and self.is_active is None
        ):
            raise ValueError("Payment method active status cannot be null.")

        return self





async def update_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    db: AsyncSession,
    payment_logo: UploadFile | None = None,
) -> dict[str, object]:
    """
    Update a payment method by slug.

    Supports:
        - Updating the payment method name.
        - Updating the internal provider code.
        - Updating the active status.
        - Replacing the payment logo.
        - Checking name and provider-code uniqueness.
        - Regenerating the slug when the name changes.

    The previous logo is deleted only after a successful database commit.
    """

    # ---------------------------------------------------------
    # Retrieve payment method by slug
    # ---------------------------------------------------------
    method = await get_payment_by_slug(
        slug=slug,
        db=db,
    )

    if method is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found.",
        )

    # ---------------------------------------------------------
    # Extract explicitly supplied fields
    # ---------------------------------------------------------
    update_data = data.model_dump(exclude_unset=True)

    if not update_data and payment_logo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were provided.",
        )

    # ---------------------------------------------------------
    # Determine changed fields
    # ---------------------------------------------------------
    new_name = update_data.get("name")
    new_provider_code = update_data.get("provider_code")

    name_changed = (
        "name" in update_data
        and new_name != method.name
    )

    provider_code_changed = (
        "provider_code" in update_data
        and new_provider_code != method.provider_code
    )

    # ---------------------------------------------------------
    # Check duplicate payment method name
    # ---------------------------------------------------------
    if name_changed and new_name is not None:
        try:
            existing_name_id = await db.scalar(
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
                    "payment_method_slug=%s",
                    slug,
                )

            logger.exception(
                "Failed to check payment method name uniqueness: "
                "payment_method_slug=%s",
                slug,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to validate payment method name.",
            ) from exc

        if existing_name_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Payment method '{new_name}' already exists.",
            )

    # ---------------------------------------------------------
    # Check duplicate provider code
    # ---------------------------------------------------------
    if provider_code_changed and new_provider_code is not None:
        try:
            existing_provider_id = await db.scalar(
                select(PaymentMethods.id).where(
                    col(PaymentMethods.provider_code)
                    == new_provider_code,
                    col(PaymentMethods.id) != method.id,
                )
            )

        except SQLAlchemyError as exc:
            try:
                await db.rollback()
            except Exception:
                logger.exception(
                    "Rollback failed after duplicate-provider query error: "
                    "payment_method_slug=%s",
                    slug,
                )

            logger.exception(
                "Failed to check payment provider code uniqueness: "
                "payment_method_slug=%s",
                slug,
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

    # ---------------------------------------------------------
    # Prepare logo replacement
    # ---------------------------------------------------------
    old_logo_key = method.payment_logo_key
    new_logo_key = old_logo_key

    if payment_logo is not None:
        # Upload without deleting the existing logo.
        # handle_file_update returns the new key.
        new_logo_key = await handle_file_update(
            file=payment_logo,
            current_key=None,
            prefix=PAYMENT_LOGO,
            max_size=PAYMENT_LOGO_MAX_SIZE,
        )

    # ---------------------------------------------------------
    # Apply changes
    # ---------------------------------------------------------
    if name_changed and new_name is not None:
        method.name = new_name
        method.slug = generate_slug(new_name)

    if provider_code_changed and new_provider_code is not None:
        method.provider_code = new_provider_code

    if "is_active" in update_data:
        method.is_active = update_data["is_active"]

    if payment_logo is not None:
        method.payment_logo_key = new_logo_key

    # ---------------------------------------------------------
    # Flush changes before committing
    # ---------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after payment method integrity error: "
                "payment_method_slug=%s",
                slug,
            )

        # Flush failed before commit, so the new logo is not referenced
        # by a successfully committed update.
        if payment_logo is not None and new_logo_key:
            try:
                await delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up uncommitted payment logo: "
                    "payment_method_slug=%s",
                    slug,
                )

        logger.exception(
            "Payment method update violated a database constraint: "
            "payment_method_slug=%s",
            slug,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The payment method name, slug, or provider code "
                "already exists."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after payment method flush error: "
                "payment_method_slug=%s",
                slug,
            )

        if payment_logo is not None and new_logo_key:
            try:
                await delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up uncommitted payment logo: "
                    "payment_method_slug=%s",
                    slug,
                )

        logger.exception(
            "Failed to flush payment method update: "
            "payment_method_slug=%s",
            slug,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update payment method.",
        ) from exc

    # ---------------------------------------------------------
    # Commit changes
    # ---------------------------------------------------------
    try:
        await db.commit()

    except IntegrityError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after payment method commit error: "
                "payment_method_slug=%s",
                slug,
            )

        # Preserve the new logo on commit exceptions because the
        # final database commit outcome may be uncertain.
        logger.exception(
            "Payment method commit encountered an integrity error: "
            "payment_method_slug=%s",
            slug,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The payment method name, slug, or provider code "
                "already exists."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after payment method commit error: "
                "payment_method_slug=%s",
                slug,
            )

        # Do not delete the new logo here. A commit exception can
        # leave the final transaction outcome uncertain.
        logger.exception(
            "Failed to commit payment method update: "
            "payment_method_slug=%s",
            slug,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update payment method.",
        ) from exc

    # ---------------------------------------------------------
    # Refresh committed record
    # ---------------------------------------------------------
    try:
        await db.refresh(method)

    except SQLAlchemyError as exc:
        logger.exception(
            "Payment method committed but refresh failed: "
            "payment_method_slug=%s",
            slug,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Payment method was updated, but its refreshed "
                "data could not be loaded."
            ),
        ) from exc

    # ---------------------------------------------------------
    # Delete old logo only after successful commit
    # ---------------------------------------------------------
    if (
        payment_logo is not None
        and old_logo_key
        and old_logo_key != new_logo_key
    ):
        try:
            await delete_from_r2(old_logo_key)

        except Exception:
            # The database update succeeded. Do not report the
            # entire update as failed because cleanup failed.
            logger.exception(
                "Payment method updated, but old logo cleanup failed: "
                "payment_method_slug=%s",
                slug,
            )

    # ---------------------------------------------------------
    # Return success response
    # ---------------------------------------------------------
    return {
        "message": "Payment method updated successfully.",
        "payment_method": to_payment_method_read(method),
        }


