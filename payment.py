# api/core/file_storage.py

LOGO_FOLDER = "home/logo"
BANNER_FOLDER = "home/banner"
PAYMENT_LOGO = "home/paymentlogo"    
# ← new


PAYMENT_LOGO_MAX_SIZE = 2 * 1024 * 1024   # 2 MiB — logos are small


# api/payments/models.py (addition to the PaymentMethods class)

payment_logo_key: str | None = Field(
    default=None,
    sa_column=Column(
        String(500),
        nullable=True,
    ),
)



# api/payments/schemas.py (the changed sections)

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



@at top of logics

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



class AvailablePaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str | None = None
    payment_logo_url: str | None = None


# api/payments/schemas.py (addition)

class AvailablePaymentMethodRead(BaseModel):
    """
    One unique payment method across the platform.

    Deduplicated by name. Represents the *first* occurrence of each
    method name found in the database — used for the public
    "We accept the following" list.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str | None = None


class AvailablePaymentMethodsRead(BaseModel):
    """All unique payment methods accepted across the platform."""
    total: int
    methods: list[AvailablePaymentMethodRead]


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




# api/payments/routes.py (the changed routes)

from fastapi import APIRouter, File, Form, UploadFile, status

# ...existing imports...


@router.post(
    "",
    response_model=CreatePaymentMethodResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment method (admin only)",
)
async def create_method(
    db: DBDep,
    admin: AdminUser,
    country_id: int = Form(...),
    name: str = Form(...),
    payment_logo: UploadFile | None = File(None),
):
    """
    Create a payment method.

    Accepts multipart/form-data so the logo can be uploaded in the
    same request:

        country_id      (required, int)
        name            (required, string)
        payment_logo    (optional, image file)
    """
    # Construct the Pydantic payload from the form fields so the
    # existing schema validators (uppercase, length, etc.) still run.
    data = PaymentMethodCreate(
        country_id=country_id,
        name=name,
    )

    return await create_payment_method(
        data=data,
        payment_logo=payment_logo,
        db=db,
        current_user=admin,
    )


@router.patch(
    "/{slug}",
    response_model=UpdatePaymentMethodResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a payment method (admin only)",
)
async def patch_payment_method(
    slug: str,
    db: DBDep,
    admin: AdminUser,
    name: str | None = Form(None),
    payment_logo: UploadFile | None = File(None),
):
    """
    Update a payment method.

    Accepts multipart/form-data:

        name            (optional, string)
        payment_logo    (optional, image file — replaces existing)
    """
    data = PaymentMethodUpdate(name=name)

    return await update_payment_method(
        slug=slug,
        data=data,
        payment_logo=payment_logo,
        db=db,
        current_user=admin,
    )





# api/payments/logics.py (addition)

async def read_available_payment_methods(
    db: AsyncSession,
) -> AvailablePaymentMethodsRead:
    """
    Return one instance of each unique payment method across the
    platform.

    Deduplicated by `name`. Used by the public "We accept the
    following payment methods" widget on the landing page,
    checkout, and anywhere a country hasn't been chosen yet.

    Public endpoint — no authentication, no permission required.
    """
    try:
        # Get the minimum id for each name.
        # Portable across Postgres / MySQL / SQLite.
        subquery = (
            select(
                col(PaymentMethods.name).label("name"),
                func.min(col(PaymentMethods.id)).label("first_id"),
            )
            .group_by(col(PaymentMethods.name))
            .subquery()
        )

        # Then fetch the full row for each first_id.
        result = await db.execute(
            select(PaymentMethods)
            .join(subquery, col(PaymentMethods.id) == subquery.c.first_id)
            .order_by(col(PaymentMethods.name))
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
        methods=[
            AvailablePaymentMethodRead.model_validate(m) for m in methods
        ],
    )




# api/payments/routes.py (addition)

@router.get(
    "/available",
    response_model=AvailablePaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List unique payment methods accepted (public)",
)
async def list_available_payment_methods(db: DBDep):
    """
    Public — no auth required.

    Returns one instance of each unique payment method across
    every country. Used by the public "We accept the following"
    list on landing / checkout pages.
    """
    return await read_available_payment_methods(db=db)





# 1. Literal path — most specific
@router.get("/available", ...)                          # ← NEW, first

# 2. Literal prefix + param
@router.get("/country/{country_slug}", ...)             # public per-country

# 3. Dynamic path — least specific
@router.get("/{slug}", ...)                             # single method (admin)




// src/types/payment/read.ts (addition)

export interface AvailablePaymentMethodRead {
  id: number;
  name: string;
  slug?: string | null;
}

export interface AvailablePaymentMethodsRead {
  total: number;
  methods: AvailablePaymentMethodRead[];
}




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








# api/payments/routes.py (the changed routes)

from fastapi import APIRouter, File, Form, UploadFile, status

# ...existing imports...


@router.post(
    "",
    response_model=CreatePaymentMethodResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment method (admin only)",
)
async def create_method(
    db: DBDep,
    admin: AdminUser,
    country_id: int = Form(...),
    name: str = Form(...),
    payment_logo: UploadFile | None = File(None),
):
    """
    Create a payment method.

    Accepts multipart/form-data so the logo can be uploaded in the
    same request:

        country_id      (required, int)
        name            (required, string)
        payment_logo    (optional, image file)
    """
    # Construct the Pydantic payload from the form fields so the
    # existing schema validators (uppercase, length, etc.) still run.
    data = PaymentMethodCreate(
        country_id=country_id,
        name=name,
    )

    return await create_payment_method(
        data=data,
        payment_logo=payment_logo,
        db=db,
        current_user=admin,
    )


@router.patch(
    "/{slug}",
    response_model=UpdatePaymentMethodResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a payment method (admin only)",
)
async def patch_payment_method(
    slug: str,
    db: DBDep,
    admin: AdminUser,
    name: str | None = Form(None),
    payment_logo: UploadFile | None = File(None),
):
    """
    Update a payment method.

    Accepts multipart/form-data:

        name            (optional, string)
        payment_logo    (optional, image file — replaces existing)
    """
    data = PaymentMethodUpdate(name=name)

    return await update_payment_method(
        slug=slug,
        data=data,
        payment_logo=payment_logo,
        db=db,
        current_user=admin,
)





// src/types/payment/read.ts

export interface PaymentMethodRead {
  id: number;
  country_id: number;
  name: string;
  slug?: string | null;
  payment_logo_url?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CountryPaymentMethodsRead {
  country_id: number;
  country_name: string;
  payment_methods: PaymentMethodRead[];
}

export interface AllPaymentMethodsRead {
  total_countries: number;
  data: CountryPaymentMethodsRead[];
}

export interface AvailablePaymentMethodRead {
  id: number;
  name: string;
  slug?: string | null;
  payment_logo_url?: string | null;
}

export interface AvailablePaymentMethodsRead {
  total: number;
  methods: AvailablePaymentMethodRead[];
}







