

# =============================================================================
# ID NARROWING HELPERS
# =============================================================================

def country_id_of(country: Country) -> int:
    if country.id is None:
        logger.error("Country row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load country. Please try again.",
        )
    return country.id


def payment_method_id_of(method: PaymentMethods) -> int:
    if method.id is None:
        logger.error("Payment method row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )
    return method.id


# =============================================================================
# SLUG HELPER
# =============================================================================

async def get_payment_by_slug(
    db: AsyncSession,
    slug: str,
) -> PaymentMethods | None:
    """Fetch a payment method by slug (name-derived, no id prefix)."""
    try:
        normalized = slug.strip().lower()
        result = await db.execute(
            select(PaymentMethods).where(
                PaymentMethods.slug == normalized
            )
        )
        return result.scalars().first()
    except Exception:
        logger.exception(
            "Failed to fetch payment method slug=%s", slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )


# =============================================================================
# CREATE — ADMIN ONLY (route enforces via `admin: AdminUser`)
# =============================================================================

async def create_payment_method(
    data: PaymentMethodCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a payment method for a country.

    Caller is guaranteed to be an admin — enforced by the route's
    `admin: AdminUser` dependency before this function runs.

    `current_user` is passed for audit logging only.
    """

    # ------------------------------------------------------------------
    # Country must exist
    # ------------------------------------------------------------------
    try:
        country = await db.get(Country, data.country_id)
    except Exception:
        logger.exception(
            "Failed to load country id=%s", data.country_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load country. Please try again.",
        )

    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {data.country_id} not found",
        )

    # ------------------------------------------------------------------
    # Uniqueness within country.
    #
    # `data.name` is normalized (uppercase, whitespace-collapsed) by
    # the schema validator, so direct equality is safe.
    # ------------------------------------------------------------------
    try:
        existing = (
            await db.execute(
                select(PaymentMethods).where(
                    col(PaymentMethods.name) == data.name,
                    col(PaymentMethods.country_id) == data.country_id,
                )
            )
        ).scalars().first()
    except Exception:
        logger.exception(
            "Failed to check existing payment method name=%s country_id=%s",
            data.name,
            data.country_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Payment method '{data.name}' already exists "
                f"in {country.name}"
            ),
        )

    # ------------------------------------------------------------------
    # Slug — globally unique, prefixed with the country slug so the
    # same method name in different countries doesn't collide.
    # ------------------------------------------------------------------
    country_slug = getattr(country, "slug", None) or str(data.country_id)
    method_slug = generate_slug(f"{country_slug}-{data.name}")

    method = PaymentMethods(
        country_id=data.country_id,
        name=data.name,
        slug=method_slug,
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
            "Failed to create payment method name=%s country_id=%s",
            data.name,
            data.country_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    logger.info(
        "Payment method '%s' (slug=%s) created in %s by admin user_id=%s",
        method.name,
        method.slug,
        country.name,
        current_user.id,
    )

    return {
        "message": (
            f"Payment method '{method.name}' created "
            f"successfully in {country.name}"
        ),
        "payment_method": PaymentMethodRead.model_validate(method),
    }


# =============================================================================
# READ — all, grouped by country (manage_payments permission)
# =============================================================================

async def read_all_payment_methods(
    db: AsyncSession,
    current_user: ReadUser,
    skip: int = 0,
    limit: int = 100,
) -> AllPaymentMethodsRead:
    """Return all payment methods grouped by country."""

    # Authorization OUTSIDE the try block so the 403 propagates.
    await has_permission(
        user=current_user,
        required_perm="manage_payments",
    )

    try:
        result = await db.execute(
            select(Country)
            .join(
                PaymentMethods,
                col(PaymentMethods.country_id) == col(Country.id),
            )
            .distinct()
            .order_by(col(Country.name))
            .offset(skip)
            .limit(limit)
        )
        countries = result.scalars().all()

        data: list[CountryPaymentMethodsRead] = []
        for country in countries:
            methods_result = await db.execute(
                select(PaymentMethods)
                .where(
                    col(PaymentMethods.country_id) == country_id_of(country)
                )
                .order_by(col(PaymentMethods.name))
            )
            methods = methods_result.scalars().all()

            data.append(
                CountryPaymentMethodsRead(
                    country_id=country_id_of(country),
                    country_name=country.name,
                    payment_methods=[
                        PaymentMethodRead.model_validate(m) for m in methods
                    ],
                )
            )
    except HTTPException:
        # Nested helpers (country_id_of) may raise HTTPException —
        # propagate as-is rather than wrapping in a 500.
        raise
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
# READ — per country (PUBLIC)
# =============================================================================

async def read_payment_methods_by_country(
    country_slug: str,
    db: AsyncSession,
) -> CountryPaymentMethodsRead:
    """
    Return payment methods for a specific country by country slug.

    Public — no authentication or permission required.
    """

    country = await get_country_by_slug(db, country_slug)

    try:
        result = await db.execute(
            select(PaymentMethods)
            .where(
                col(PaymentMethods.country_id) == country_id_of(country)
            )
            .order_by(col(PaymentMethods.name))
        )
        methods = result.scalars().all()
    except HTTPException:
        raise
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
        country_id=country_id_of(country),
        country_name=country.name,
        payment_methods=[
            PaymentMethodRead.model_validate(m) for m in methods
        ],
    )


# =============================================================================
# READ — single (manage_payments permission, country-scoped)
# =============================================================================

async def read_single_payment_method(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> PaymentMethodRead:
    """Get a single payment method by slug. Country-scoped permission."""

    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    await has_permission(
        user=current_user,
        required_perm="manage_payments",
        target_country_id=method.country_id,
    )

    return PaymentMethodRead.model_validate(method)


# =============================================================================
# UPDATE — ADMIN ONLY (route enforces via `admin: AdminUser`)
# =============================================================================

async def update_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update a payment method by slug.

    Caller is guaranteed to be an admin — enforced by the route's
    `admin: AdminUser` dependency before this function runs.

    Only `name` is updatable.
    Returns { message, payment_method }.
    """

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    # ------------------------------------------------------------------
    # Reject empty update payload
    # ------------------------------------------------------------------
    if all(value is None for value in (data.name,)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ------------------------------------------------------------------
    # Track actual changes
    # ------------------------------------------------------------------
    updated_fields: list[str] = []

    if data.name is not None and data.name != method.name:
        # `data.name` is already normalized by the schema validator.
        try:
            existing = (
                await db.execute(
                    select(PaymentMethods).where(
                        col(PaymentMethods.name) == data.name,
                        col(PaymentMethods.country_id)
                        == method.country_id,
                        col(PaymentMethods.id)
                        != payment_method_id_of(method),
                    )
                )
            ).scalars().first()
        except HTTPException:
            raise
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
    # Reject no-op
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
    # Persist
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
        "Payment method id=%s updated by admin user_id=%s (fields=%s)",
        payment_method_id_of(method),
        current_user.id,
        ",".join(updated_fields),
    )

    return {
        "message": f"Payment method '{method.name}' updated successfully",
        "payment_method": PaymentMethodRead.model_validate(method),
    }


# =============================================================================
# DELETE — ADMIN ONLY (route enforces via `admin: AdminUser`)
# =============================================================================

async def delete_payment_method(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a payment method by slug.

    Caller is guaranteed to be an admin — enforced by the route's
    `admin: AdminUser` dependency before this function runs.
    """

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
        logger.exception(
            "Failed to delete payment method slug=%s", slug
        )
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






@router.get(
    "/country/{country_slug}",
    response_model=CountryPaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List payment methods for a country (public)",
)
async def list_payment_methods_by_country(
    country_slug: str,
    db: DBDep,
):
    # No CurrentUser dependency — public endpoint.
    return await read_payment_methods_by_country(
        country_slug=country_slug, db=db
    )


# =============================================================================
# AUTHENTICATED READS — permission-gated in the service
# =============================================================================

@router.get(
    "",
    response_model=AllPaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List all payment methods grouped by country (manage_payments)",
)
async def list_all_payment_methods(
    db: DBDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_payment_methods(
        db=db,
        current_user=current_user,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{slug}",
    response_model=PaymentMethodRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single payment method (manage_payments)",
)
async def get_payment_method(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_single_payment_method(
        slug=slug, db=db, current_user=current_user
    )


# =============================================================================
# ADMIN-ONLY WRITES
#
# `admin: AdminUser` rejects non-admins with 403 before the service
# function is called. The service trusts the caller is an admin.
# =============================================================================

@router.post(
    "",
    response_model=CreatePaymentMethodResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment method (admin only)",
)
async def create_method(
    data: PaymentMethodCreate,
    db: DBDep,
    admin: AdminUser,
):
    return await create_payment_method(
        data=data, db=db, current_user=admin
    )


@router.patch(
    "/{slug}",
    response_model=UpdatePaymentMethodResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a payment method (admin only)",
)
async def patch_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    db: DBDep,
    admin: AdminUser,
):
    return await update_payment_method(
        slug=slug, data=data, db=db, current_user=admin
    )


@router.delete(
    "/{slug}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a payment method (admin only)",
)
async def remove_payment_method(
    slug: str,
    db: DBDep,
    admin: AdminUser,
):
    return await delete_payment_method(
        slug=slug, db=db, current_user=admin
    )







