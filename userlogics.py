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
    # ==============================================================

    db.add(user)
    await db.commit()
    await db.refresh(user)

    # ==============================================================
    # 6. Log update
    # ==============================================================

    logger.info(
        f"User {current_user.id} updated: "
        f"{', '.join(updated_fields)}"
    )

    # ==============================================================
    # 7. Return updated user
    # ==============================================================

    return ReadUser.model_validate(user)

And the route remains:

@router.patch(
    "/profile/names",
    response_model=ReadUser,
    status_code=status.HTTP_200_OK,
)
async def update_names(
    data: UpdateNames,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_user_names(
        data=data,
        db=db,
        current_user=current_user,
    )

With:

CurrentUser = Annotated[
    ReadUser,
    Depends(get_authenticated_user),
]




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
