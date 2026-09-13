async def update_user_names(
    data: UpdateNames,
    db: AsyncSession,
    current_user: ReadUser,
) -> ReadUser:
    """
    Update authenticated user's surname and/or othernames.

    - Requires an authenticated user.
    - Rejects a completely empty update payload.
    - Only updates fields that actually changed.
    - Rejects the request if no actual changes were made.
    - Returns the updated user.
    """

    # ==============================================================
    # 1. Reject completely empty update payload
    # ==============================================================

    if all(
        value is None
        for value in (
            data.surname,
            data.othernames,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ==============================================================
    # 2. Get the authenticated user from the database
    # ==============================================================

    user = await get_user_by_id(
        db,
        current_user.id,
    )

    if  user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # ==============================================================
    # 3. Update only fields that actually changed
    # ==============================================================

    updated_fields: list[str] = []

    if data.surname is not None:
        if data.surname != user.surname:
            user.surname = data.surname
            updated_fields.append("surname")

    if data.othernames is not None:
        if data.othernames != user.othernames:
            user.othernames = data.othernames
            updated_fields.append("othernames")

    # ==============================================================
    # 4. Reject if nothing actually changed
    # ==============================================================

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ==============================================================
    # 5. Save changes
    # # ==============================================================

    # try:
    #     await db.delete(user)
    #     await db.commit()

    # except Exception as exc:
    #     await db.rollback()

    #     logger.exception(
    #         "Failed to permanently delete user_id=%s "
    #         "from database",
    #         user_id,
    #     )

    #     raise HTTPException(
    #         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    #         detail="Failed to delete account. Please try again later.",
    #     ) from exc

    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
        
    except Exception as e:
        await db.rollback()
        logger.exception("Cannot complete username update")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="error occured while updating your name"
        )

   
    logger.info(
        f"User {current_user.id} updated: "
        f"{', '.join(updated_fields)}"
    )

    # ==============================================================
    # 7. Return updated user
    # ==============================================================

    return ReadUser.model_validate(user)

# CHANGE PASSWORD

async def change_password(
    data: UpdatePassword,
    db: AsyncSession,
    redis:Redis,
    current_user: ReadUser,
) -> dict:
    """Change user password."""
    user = await get_user_by_id(db, current_user.id)
    if  user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect"
        )
    
    user.hashed_password = hash_password(data.new_password)
    db.add(user)
    await db.commit()
    # force relogin on every devices
    await revoke_all_user_tokens(current_user.id, redis)
    return {"message": "Password updated successfully"}


