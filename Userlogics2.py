
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
        #current_user=admin,
    )

