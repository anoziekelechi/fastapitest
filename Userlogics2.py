async def handle_disabled_account(
    data: ContactAdminMessage,
    db: AsyncSession,
    redis: Redis,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Handle contact form from disabled users.
    """

    user = await get_user_by_email(db, data.email)

    # Generic response – don't reveal account status
    if not user or not user.disabled:
        return {
            "message": "If your account exists, "
                       "your message has been sent to our support team."
        }

    user_id = get_user_id(user)
    
    # Rate limit – max 3 per hour
    rate_key = f"contact_admin_rate:{user_id}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, 
            int(timedelta(minutes=settings.contact_rate_window).total_seconds()),
            ) #3600
    if count > settings.contact_rate_limit:
        ttl = await redis.ttl(rate_key)
        default_window = int(timedelta(minutes=settings.contact_rate_window).total_seconds())
        retry_after = (
            ttl if isinstance(ttl,int) and ttl > 0 else default_window
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again in 1 hour.",
            headers={"Retry-After":str(retry_after)},
        )

    # Extract Primitive from ORM objects
    full_names = user.full_names
    # Resolve support email (country → fallback)
    support_email = settings.mail_username

    # i commented this since no country no longer exist
    # if user.country_id:
    #     country = await db.get(Country, user.country_id)
    #     if country and country.email_support:
    #         support_email = country.email_support

    background_tasks.add_task(
        send_support_message,
        user_full_names=full_names,
        support_email=support_email,
        user_email=data.email,
        message=data.message,
        mailer=mailer,
    )

    logger.info(
        "Disabled user %s (id=%s) contacted support → %s",
        data.email,
        user_id,
        support_email,
    )

    return {
        "message": "Your message has been sent to our support team. "
                   "We will review your account and get back to you."
    }




#=================================================================
async def get_user_profile(
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Load full user + relationships, then build profile dict
    with role-based fields.
    """
    result = await db.execute(
        select(User)
        .options(
            #selectinload(User.country),  # type: ignore[arg-type] commented this too
            selectinload(User.group),    # type: ignore[arg-type]
        )
        .where(User.id == current_user.id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    profile: dict = {
        "id": user.id,
        "email": user.email,
        "surname": user.surname,
        "othernames": user.othernames,
        #"country": user.country.name if user.country else None,
        "verified": user.verified,
        "disabled": user.disabled,
        "date_verified": user.date_verified,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "full_names": user.full_names,
        "avatar": user.avatar
    }

    # Group info (only if user belongs to a group)
    if user.group:
        profile["name"] = user.group.name
        profile["permission"] = user.group.permission

    # Admin-only field
    if user.is_admin:
        profile["is_admin"] = True

    return profile



# PAYMENT 
 Payment 

class PaymentMethodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=2, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Payment method name cannot be empty")
        return normalize_payment_option(v)
        


class PaymentMethodUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.strip():
            raise ValueError("Payment method name cannot be empty")
        return normalize_payment_option(v)


# =============================================================================
# READ
# =============================================================================


class PaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    country_id: int
    name: str
    slug: str | None = None

    # Store the R2 key internally, but expose a full URL to the client.
    # This field is NOT populated from the ORM directly — the service
    # layer sets it after computing `get_public_url(key)`.
    payment_logo_url: str | None = None

    created_at: datetime
    updated_at: datetime


class CountryPaymentMethodsRead(BaseModel):
    country_id: int
    country_name: str
    payment_methods: list[PaymentMethodRead]


class AllPaymentMethodsRead(BaseModel):
    total_countries: int
    data: list[CountryPaymentMethodsRead]


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreatePaymentMethodResponse(BaseModel):
    message: str
    payment_method: PaymentMethodRead


class UpdatePaymentMethodResponse(BaseModel):
    message: str
    payment_method: PaymentMethodRead





# async def _load_methods_for_country(
#     db: AsyncSession,
#     country_id: int,
# ) -> list[PaymentMethods]:
#     """Load payment methods for a country by id, ordered by name."""
#     result = await db.execute(
#         select(PaymentMethods)
#         .where(col(PaymentMethods.country_id) == country_id)
#         .order_by(col(PaymentMethods.name))
#     )
#     return list(result.scalars().all())


# async def _country_by_name(
#     db: AsyncSession,
#     name: str,
# ) -> Country | None:
#     """Case-insensitive country lookup by name."""
#     result = await db.execute(
#         select(Country).where(
#             func.lower(col(Country.name)) == name.lower()
#         )
#     )
#     return result.scalars().first()

def to_payment_method_read(method: PaymentMethods) -> PaymentMethodRead:
    """Build a PaymentMethodRead with the logo URL resolved."""
    read = PaymentMethodRead.model_validate(method)
    return read.model_copy(
        update={
            "payment_logo_url": (
                get_public_url(method.payment_logo_key)
                if method.payment_logo_key
                else None
            ),
        }
    )


# =============================================================================
# ID NARROWING HELPERS
# =============================================================================

# def country_id_of(country: Country) -> int:
#     if country.id is None:
#         logger.error("Country row has no id — data integrity issue")
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Failed to load country. Please try again.",
#         )
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
    payment_logo: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a payment method for a country.

    Admin only — enforced by the route's `admin: AdminUser` dependency.

    `payment_logo` is optional. If provided, it's uploaded to R2
    before the DB insert; if the insert fails, the file is deleted
    to avoid orphaning it.
    """

    # ------------------------------------------------------------------
    # Country must exist
    # ------------------------------------------------------------------
    # try:
    #     country = await db.get(Country, data.country_id)
    # except Exception:
    #     logger.exception(
    #         "Failed to load country id=%s", data.country_id
    #     )
    #     raise HTTPException(
    #         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    #         detail="Failed to load country. Please try again.",
    #     )

    # if not country:
    #     raise HTTPException(
    #         status_code=status.HTTP_404_NOT_FOUND,
    #         detail=f"Country with ID {data.country_id} not found",
    #     )

    # ------------------------------------------------------------------
    # Uniqueness within country
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
    # Slug — globally unique, prefixed with the country slug
    # ------------------------------------------------------------------
    country_slug = getattr(country, "slug", None) or str(data.country_id)
    method_slug = generate_slug(f"{country_slug}-{data.name}")

    method = PaymentMethods(
        country_id=data.country_id,
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
            "Failed to create payment method name=%s country_id=%s",
            data.name,
            data.country_id,
        )

        # Best-effort cleanup of the uploaded logo
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
        "payment_method": to_payment_method_read(method),
    }



# =============================================================================
# READ — all, grouped by country admin only
# =============================================================================

async def read_all_payment_methods(
    db: AsyncSession,
   # current_user: ReadUser, #remove
    skip: int = 0,
    limit: int = 100,
) -> AllPaymentMethodsRead:
    """Return all payment methods grouped by country."""


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
                        to_payment_method_read(m) for m in methods
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


# # =============================================================================
# # READ — single (manage_payments admin ) corrected
# # =============================================================================

async def read_single_payment_method(
    slug: str,
    db: AsyncSession,
) -> PaymentMethodRead:
    """Get a single payment method by slug. Country-scoped permission."""

    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    return to_payment_method_read(method)
#     #return PaymentMethodRead.model_validate(method)


# =============================================================================
# UPDATE — ADMIN ONLY (route enforces via `admin: AdminUser`)
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
    # Reject a completely empty update
    # ------------------------------------------------------------------
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
                        col(PaymentMethods.country_id)
                        == method.country_id,
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
    # Logo replacement
    #
    # Upload the new file first, remember the old key so we can delete
    # it after the DB commit succeeds.
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

        # Roll back the new logo if it was uploaded
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

    # ------------------------------------------------------------------
    # Delete the old logo AFTER successful commit
    # ------------------------------------------------------------------
    if (
        new_logo_key
        and old_logo_key
        and new_logo_key != old_logo_key
    ):
        try:
            delete_from_r2(old_logo_key)
        except Exception:
            # Non-fatal — the DB row references the new key; the old
            # file is orphaned in R2 but harmless.
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






async def read_resolved_payment_methods(
    db: AsyncSession,
    current_user: ReadUser | None,
    country_name: str,
) -> CountryPaymentMethodsRead:
    """
    Resolve payment methods by priority, skipping empty tiers:

        1. Authenticated user's country  — if it has methods
        2. The passed-in `country_name`  — if it has methods
        3. Default country (Liberia)     — if it has methods
        4. Empty                         — no country has methods

    `country_name` is the *already-resolved* name from
    `get_country_name_from_host` — this function does not inspect
    HTTP headers.

    Public — no authentication required.
    """
    from api.core.country import DEFAULT_COUNTRY_NAME

    # ------------------------------------------------------------------
    # Build the candidate list in priority order, deduped
    # ------------------------------------------------------------------
    candidates: list[Country] = []

    # 1. Authenticated user's country
    if current_user is not None and current_user.country_id:
        try:
            user_country = await db.get(Country, current_user.country_id)
            if user_country is not None:
                candidates.append(user_country)
        except Exception:
            logger.exception(
                "Failed to load user country id=%s",
                current_user.country_id,
            )

    # 2. Host-resolved country (already resolved by the route)
    try:
        host_country = await _country_by_name(db, country_name)
        if host_country is not None and all(
            country_id_of(c) != country_id_of(host_country)
            for c in candidates
        ):
            candidates.append(host_country)
    except Exception:
        logger.exception(
            "Failed to load host-resolved country name=%s", country_name
        )

    # 3. Default country
    if all(
        c.name.lower() != DEFAULT_COUNTRY_NAME.lower()
        for c in candidates
    ):
        try:
            default_country = await _country_by_name(
                db, DEFAULT_COUNTRY_NAME
            )
            if default_country is not None:
                candidates.append(default_country)
        except Exception:
            logger.exception(
                "Failed to load default country name=%s",
                DEFAULT_COUNTRY_NAME,
            )

    # ------------------------------------------------------------------
    # Walk candidates; return the first with methods
    # ------------------------------------------------------------------
    for country in candidates:
        try:
            methods = await _load_methods_for_country(
                db, country_id_of(country)
            )
        except Exception:
            logger.exception(
                "Failed to load methods for country id=%s",
                country_id_of(country),
            )
            continue

        if methods:
            return CountryPaymentMethodsRead(
                country_id=country_id_of(country),
                country_name=country.name,
                payment_methods=[
                    to_payment_method_read(m) for m in methods
                ],
            )

    # ------------------------------------------------------------------
    # No tier had methods
    # ------------------------------------------------------------------
    return CountryPaymentMethodsRead(
        country_id=0,
        country_name=DEFAULT_COUNTRY_NAME,
        payment_methods=[],
    )












