


 ✅ Check email_support uniqueness
    if data.email_support is not None:
        existing_email = (
            await db.execute(
                select(Country).where(
                    Country.email_support == data.email_support
                )
            )
        ).scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Support email '{data.email_support}' is already "
                       f"used by '{existing_email.name}'"
            )

    # ✅ Check whatsapp uniqueness
    if data.whatsapp is not None:
        existing_whatsapp = (
            await db.execute(
                select(Country).where(
                    Country.whatsapp == data.whatsapp
                )
            )
        ).scalars().first()
        if existing_whatsapp:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"WhatsApp number '{data.whatsapp}' is already "
                       f"used by '{existing_whatsapp.name}'"
            )
