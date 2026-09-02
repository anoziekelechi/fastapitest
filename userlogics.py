
async def update_user_names(
    data: UpdateNames,
    db: AsyncSession,
    current_user: ReadUser,
) -> ReadUser:
    """
    Update authenticated user's surname and/or othernames.
    
    - At least one field must be provided
    - Only updates fields that differ from current values
    - Requires authentication (enforced at route level)
    """
    if data.surname is None and data.othernames is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field (surname or othernames) must be provided"
        )
    
    user = await get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    updated_fields = []
    
    if data.surname is not None:
        if data.surname == user.surname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New surname is the same as current surname"
            )
        user.surname = data.surname
        updated_fields.append("surname")
    
    if data.othernames is not None:
        if data.othernames == user.othernames:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New othernames is the same as current othernames"
            )
        user.othernames = data.othernames
        updated_fields.append("othernames")
    
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    logger.info(f"User {current_user.id} updated: {', '.join(updated_fields)}")
    
    return ReadUser.model_validate(user)
