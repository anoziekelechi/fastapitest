
# =============================================================================
# READ — all, grouped by country permision required
# =============================================================================

async def read_all_payment_methods(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> AllPaymentMethodsRead:
    """
    Return all payment methods grouped by country.
    """

    try:
        # Countries that have at least one payment method
        result = await db.execute(
            select(Country)
            .join(PaymentMethods, PaymentMethods.country_id == Country.id)#ument of type "bool" cannot be assigned to parameter "onclause" of type "_OnClauseArgument | None" in function "join"
            .distinct()
            .order_by(Country.name)
            .offset(skip)
            .limit(limit)
        )
        countries = result.scalars().all()

        data: list[CountryPaymentMethodsRead] = []
        for country in countries:
            methods_result = await db.execute(
                select(PaymentMethods)
                .where(PaymentMethods.country_id == country.id)
                .order_by(PaymentMethods.name)
            )
            methods = methods_result.scalars().all()

            data.append(
                CountryPaymentMethodsRead(
                    country_id=country.id,
                    country_name=country.name,
                    payment_methods=[
                        PaymentMethodRead.model_validate(m) for m in methods
                    ],
                )
            )
    except Exception:
        logger.exception("Failed to list payment methods")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        )

    return AllPaymentMethodsRead(
        total_countries=len(data),
        data=data,
    )


# =============================================================================
# READ — per country available for public to select from
# =============================================================================

async def read_payment_methods_by_country(
    country_slug: str,
    db: AsyncSession,
) -> CountryPaymentMethodsRead:
    """
    Return payment methods for a specific country by country slug.
    """

    country = await get_country_by_slug(db, country_slug)

    try:
        result = await db.execute(
            select(PaymentMethods)
            .where(PaymentMethods.country_id == country.id)
            .order_by(PaymentMethods.name)
        )
        methods = result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load payment methods for country slug=%s",
            country_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        )

    return CountryPaymentMethodsRead(
        country_id=country.id,# type "int | None" is not assignable to type "int"
        country_name=country.name,
        payment_methods=[
            PaymentMethodRead.model_validate(m) for m in methods
        ],
    )


# =============================================================================
# READ — single protected by permission
# =============================================================================

async def read_single_payment_method(
    slug: str,
    db: AsyncSession,
) -> PaymentMethodRead:
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )
    return PaymentMethodRead.model_validate(method)


# =============================================================================
# UPDATE require permission too not only admin
# =============================================================================

async def update_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update a payment method by slug. Country-scoped.

    Only `name` is updatable.

    Returns:
        { message, payment_method }
    """

    # ------------------------------------------------------------------
    # 1. Load the method
    # ------------------------------------------------------------------
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    # ------------------------------------------------------------------
    # 2. Country-scoped permission
    # ------------------------------------------------------------------
    await has_permission(
        user=current_user,
        required_perm="manage_payments",
        target_country_id=method.country_id,
    )

    # ------------------------------------------------------------------
    # 3. Reject empty update payload
    # ------------------------------------------------------------------
    if all(value is None for value in (data.name,)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ------------------------------------------------------------------
    # 4. Track actual changes
    # ------------------------------------------------------------------
    updated_fields: list[str] = []

    if data.name is not None and data.name != method.name:
        # 4a. Uniqueness within the country
        try:
            existing = (
                await db.execute(
                    select(PaymentMethods).where(
                        func.lower(PaymentMethods.name) == data.name.lower(),
                        PaymentMethods.country_id == method.country_id,
                        PaymentMethods.id != method.id,
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
                detail=(
                    f"Payment method '{data.name}' already exists "
                    f"in this country"
                ),
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
    # 5. Reject no-op update
    # ------------------------------------------------------------------
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ------------------------------------------------------------------
    # 6. Persist
    # ------------------------------------------------------------------
    try:
        db.add(method)
        await db.commit()
        await db.refresh(method)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to update payment method slug=%s", slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update payment method. Please try again.",
        )

    logger.info(
        "Payment method id=%s updated by user_id=%s (fields=%s)",
        method.id,
        current_user.id,
        ",".join(updated_fields),
    )

    return {
        "message": f"Payment method '{method.name}' updated successfully",
        "payment_method": PaymentMethodRead.model_validate(method),
    }


# =============================================================================
# DELETE require permission 
# =============================================================================

async def delete_payment_method(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a payment method by slug. Admin only.
    """

    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete payment methods",
            headers={"X-Error-Code": "not_admin"},
        )

    method_name = method.name

    try:
        await db.delete(method)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to delete payment method slug=%s", slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete payment method. Please try again.",
        )

    logger.info(
        "Payment method '%s' deleted by user_id=%s",
        method_name,
        current_user.id,
    )

    return {"message": f"Payment method '{method_name}' deleted successfully"}




