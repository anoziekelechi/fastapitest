from fastapi import APIRouter, File, Form, UploadFile, status

from api.core.database import DBDep
from api.payments.logics import (
    create_payment_method,
    delete_payment_method,
    read_all_payment_methods,
    read_available_payment_methods,
    read_payment_methods_by_country,
    read_single_payment_method,
    update_payment_method,
)
from api.payments.schemas import (
    AllPaymentMethodsRead,
    AvailablePaymentMethodsRead,
    CountryPaymentMethodsRead,
    CreatePaymentMethodResponse,
    MessageResponse,
    PaymentMethodCreate,
    PaymentMethodRead,
    PaymentMethodUpdate,
    UpdatePaymentMethodResponse,
)
from api.users.deps import AdminUser, CurrentUser

router = APIRouter(prefix="/payment-methods", tags=["Payment Methods"])


# =============================================================================
# PUBLIC
# =============================================================================

@router.get(
    "/available",
    response_model=AvailablePaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List unique payment methods accepted (public)",
)
async def list_available_payment_methods(db: DBDep):
    return await read_available_payment_methods(db=db)


@router.get(
    "/country/{country_slug}",
    response_model=CountryPaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List payment methods for a country (authenticated)",
)
async def list_payment_methods_by_country(
    country_slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_payment_methods_by_country(
        country_slug=country_slug,
        db=db,
        current_user=current_user,
    )


# =============================================================================
# ADMIN READS
# =============================================================================

@router.get(
    "",
    response_model=AllPaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List all payment methods grouped by country (admin only)",
)
async def list_all_payment_methods(
    db: DBDep,
    admin: AdminUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_payment_methods(
        db=db,
        current_user=admin,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{slug}",
    response_model=PaymentMethodRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single payment method (admin only)",
)
async def get_payment_method(
    slug: str,
    db: DBDep,
    admin: AdminUser,
):
    return await read_single_payment_method(
        slug=slug, db=db, current_user=admin
    )


# =============================================================================
# ADMIN WRITES — multipart
#
# FastAPI reads each field individually with Form(...) / File(...),
# then we rebuild the Pydantic model inside the route body so the
# schema validators run (uppercase name, length checks, etc.).
# =============================================================================

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
    # Reconstruct the Pydantic model so validators run.
    # If validation fails, FastAPI returns a 422 with the standard
    # `detail` array — same shape every other endpoint uses.
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
    data = PaymentMethodUpdate(name=name)

    return await update_payment_method(
        slug=slug,
        data=data,
        payment_logo=payment_logo,
        db=db,
        current_user=admin,
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





# api/payments/logics.py (addition)



# api/payments/routes.py (addition)







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







