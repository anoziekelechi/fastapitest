from api.users.deps import AdminUser
from fastapi import(
    HTTPException,
    status, 
    UploadFile, 
    File,
    APIRouter,
    status, 
    Form,
    BackgroundTasks,
    Response
    )
from api.core.database import DBDep
from api.core.mail import MailDep
from api.payments.logics import (
    read_all_payment_methods,
    read_available_payment_methods,
    read_single_payment_method,
    create_payment_method,
    delete_payment_method,
    update_payment_method,
    # create_receipt,
    # read_firm_receipts,
    # read_all_receipts_grouped,
    # read_my_receipts,
    # read_single_receipt,
    # render_receipt_pdf,
    # export_firm_receipts_csv,
    # read_all_receipts_grouped
    
)
from api.payments.schemas import (
    AvailablePaymentMethodsRead,
    AdminPaymentMethodRead,
    AdminAvailablePaymentMethodsRead,
    UpdatePaymentMethodResponse,
    CreatePaymentMethodResponse,
    PaymentMethodCreate,
    PaymentMethodRead
    MessageResponse,
    PaymentMethodUpdate
    


)
from api.users.deps import CurrentUser

router = APIRouter(prefix="/receipts", tags=["Receipts"])


# PAYMENT OPTION


# =============================================================================
# PUBLIC
#
# Declared BEFORE /{slug} so `/available` doesn't get swallowed by
# the dynamic path.
# =============================================================================

@router.get(
    "/available",
    response_model=AvailablePaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List payment methods accepted (public)",
)
async def list_available_payment_methods(db: DBDep):
    """
    Public — no authentication.

    Used by the Footer widget and landing page to show the payment
    methods the platform supports.
    """
    return await read_available_payment_methods(db=db)


# =============================================================================
# ADMIN READS
# =============================================================================

@router.get(
    "",
    response_model=AvailablePaymentMethodsRead,
    status_code=status.HTTP_200_OK,
    summary="List all payment methods (admin only)",
)
async def list_all_payment_methods(
    db: DBDep,
    admin: AdminUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_payment_methods(
        db=db, skip=skip, limit=limit
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
    return await read_single_payment_method(slug=slug, db=db)


# =============================================================================
# ADMIN WRITES — multipart (logo upload)
# =============================================================================

@router.post(
    "/add/payments-methods",
    response_model=CreatePaymentMethodResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment method (admin only)",
)
async def create_method(
    db: DBDep,
    admin: AdminUser,
    name: str = Form(...),
    provider_code: str = Form(...),
    payment_logo: UploadFile | None = File(None),
):
    data = PaymentMethodCreate(name=name,provider_code=provider_code)

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
    is_active:bool,
    name: str | None = Form(None),
    payment_logo: UploadFile | None = File(None),
):
    data = PaymentMethodUpdate(name=name,is_active=is_active)

    return await update_payment_method(
        data=data,
        slug=slug,
        payment_logo=payment_logo,
        db=db,
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

