
async def delete_country(
    country_id: int,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a country.

    Admin only.

    Flow:
        1. Fetch country (404 if not found)
        2. Check no users are assigned to this country
        3. Check no offices are assigned to this country
        4. Delete

    Args:
        country_id: ID of country to delete
        db: Database session
        current_user: Authenticated admin user

    Returns:
        dict: Success message

    Raises:
        HTTPException: 404 if not found
        HTTPException: 400 if country has assigned users or offices
    """
    country = await get_country_by_id(db, country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with ID {country_id} not found"
        )
    
    # Prevent deletion if users are assigned
    if country.users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete '{country.name}' - it has "
                   f"{len(country.users)} assigned user(s). "
                   f"Reassign or remove users first."
        )
    
    # Prevent deletion if offices are assigned
    if country.offices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete '{country.name}' - it has "
                   f"{len(country.offices)} assigned office(s). "
                   f"Remove offices first."
        )
    
    country_name = country.name
    await db.delete(country)
    await db.commit()
    
    logger.info(
        f"Country '{country_name}' (id={country_id}) "
        f"deleted by admin {current_user.id}"
    )
    
    return {"message": f"Country '{country_name}' deleted successfully"}

