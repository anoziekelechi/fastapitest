

@router.post(
    "/add_country",
    status_code=status.HTTP_201_CREATED,
    summary="Create a country (admin only)",
)
async def create_country_route(
    data: CountryCreate,
    db: DBDep,
    current_user: ReadUser = AdminUser,             # ✅ Admin only
) -> dict:
    """
    Create a new country.

    Admin only. Country name is normalized to Title Case.
    Multiple spaces are collapsed. Numbers and special chars rejected.

    Examples:
        "liberia"         → "Liberia"
        "south africa"    → "South Africa"
        "guinea  bissau"  → "Guinea Bissau"
    """
    return await create_country(
        data=data,
        db=db,
        current_user=current_user,
    )




@router.get(
    "/countries",
    response_model=CountryListRead,
    status_code=status.HTTP_200_OK,
    summary="List all countries",
)
async def list_countries(
    db: DBDep,
    skip: int = Query(default=0, ge=0, description="Records to skip"),
    limit: int = Query(default=100, ge=1, le=500, description="Max records"),
) -> CountryListRead:
    """
    List all countries with pagination.
    Public endpoint - no authentication required.
    """
    return await read_all_countries(
        db=db,
        skip=skip,
        limit=limit,
    )
    
    
    
    

@router.get(
    "/{country_id}",
    response_model=CountryRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single country",
)
async def get_country(
    country_id: int,
    db: DBDep,
) -> CountryRead:
    """
    Get a single country by ID.

    Public endpoint - no authentication required.
    """
    return await read_single_country(
        country_id=country_id,
        db=db,
    )
    
    
    
    
@router.put(
    "/{country_id}",
    response_model=CountryRead,
    status_code=status.HTTP_200_OK,
    summary="Update a country (admin only)",
)
async def update_country_route(
    country_id: int,
    data: CountryUpdate,
    db: DBDep,
    current_user: ReadUser = AdminUser,             # ✅ Admin only
) -> CountryRead:
    """
    Update a country.

    Admin only. All fields are optional - only provided fields are updated.
    """
    return await update_country(
        country_id=country_id,
        data=data,
        db=db,
        current_user=current_user,
    )


@router.delete(
    "/{country_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a country (admin only)",
)
async def delete_country_route(
    country_id: int,
    db: DBDep,
    current_user: ReadUser = AdminUser,             # ✅ Admin only
) -> dict:
    """
    Delete a country.

    Admin only. Blocked if country has assigned users or offices.
    """
    return await delete_country(
        country_id=country_id,
        db=db,
        current_user=current_user,
    )





