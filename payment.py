



# =============================================================================
# UPDATE — ADMIN ONLY
# =============================================================================






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




