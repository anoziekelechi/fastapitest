async def disable_firm(
    data: FirmAction,
    db: AsyncSession,
) -> dict:
    """
    Disable a firm account.
    
    Raises:
        404: firm not found
        400: firm already disabled
    """
    firm = await get_firm_by_email(db, data.email)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"firm '{data.name}' not found"
        )
    
    if firm.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.name}' is already disabled"
        )
    
    firm.disabled = True
    await db.commit()
    await db.refresh(user)
    
    return {"message": f"Firm '{data.name}' has been disabled"}


async def enable_user(
    data: FirmAction,
    db: AsyncSession,
) -> dict:
    """
    Re-enable a disabled firm account.
    permission require "manage_countries"
    Raises:
        404: firm not found
        400: Firm already active
    """
    firm = await get_firm_by_name (db, data.name)
   
   
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{data.name}' not found"
        )
    
    if not firm.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User '{data.email}' is already active"
        )
    
    firm.disabled = False
    await db.commit()
    await db.refresh(user)
    
    return {"message": f"Firm '{data.name}' has been re-activated"}



