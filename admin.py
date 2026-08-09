#home


async def setup_home_logic(
    sitename: str,
    db: AsyncSession,
    aboutus: str | None = None,
    intro: Optional[str] = None,
    mission: Optional[str] = None,
    vision: Optional[str] = None,
    logo_key: Optional[UploadFile] = None,
    banner_key: UploadFile | None = None,
   
) -> dict[str, str]:
    """
    Create or update MAIN home configuration
    Returns response with public image URLs
    """
    stmt = select(Home).where(Home.config_type == "MAIN")
    current = (await db.execute(stmt)).scalars().first()

    # Handle file updates (with env-specific prefix)
    new_logo_key = await handle_file_update(
        file=logo_key,
        current_key=current.logo_key if current else None,
        prefix= LOGO_FOLDER,
        max_size=LOGO_MAX_SIZE,
        validator=validate_image_file_securely,
    )

    new_banner_key = await handle_file_update(
        file=banner_key,
        current_key=current.banner_key if current else None,
        prefix= BANNER_FOLDER,
        max_size=HERO_MAX_SIZE,
        validator=validate_image_file_securely,
    )

    try:
        if current is None:
            # First time creation
            record = Home(
                config_type="MAIN",
                sitename=sitename,
                aboutus=aboutus,
                intro=intro,
                mission=mission,
                vision=vision,
                logo_key=new_logo_key,
                banner_key=new_banner_key,
            )
            db.add(record)
        else:
            # Partial update
            current.sitename = sitename

            if aboutus is not None:
                current.aboutus = aboutus
            if intro is not None:
                current.intro = intro
            if mission is not None:
                current.mission = mission
            if vision is not None:
                current.vission = vision

            # Only update file keys if new file was provided
            if logo_key is not None:
                current.logo_key = new_logo_key
            if banner_key is not None:
                current.banner_key = new_banner_key

            record = current

        await db.commit()
        await db.refresh(record)

        return {"message":"Home settings updated successfully", "status":"success"}

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save home configuration: {str(e)}"
        ) from e

#home route

@router.post( "/setup",status_code=status.HTTP_201_CREATED,response_model=ReadHome,)
async def setup_home(
    db: DBDep,
    sitename: Annotated[str, Form(min_length=1, max_length=120)],
    aboutus: Annotated[str | None, Form(max_length=2000)] = None,
    intro: Annotated[str | None, Form(max_length=1200)] = None,
    mission: Annotated[str | None, Form(max_length=1200)] = None,
    vision: Annotated[str | None, Form(max_length=1200)] = None,
    
    logo_key: Annotated[UploadFile | None, File()] = None,
    banner_key: Annotated[UploadFile | None, File()] = None,
) -> dict[str, str]:
    """
    Endpoint for admin to configure home page settings
    """
    return await setup_home_logic(
        db=db,
        sitename=sitename,
        aboutus=aboutus,
        intro=intro,
        mission=mission,
        vision=vision,
        logo_key=logo_key,
        banner_key=banner_key,
    )


#logics.py

@router.post(
    "/add_country",
    status_code=status.HTTP_201_CREATED,
    summary="Create a country (admin only)",
)
async def create_country_route(
    data: CountryCreate,
    db: DBDep,
    #current_user: ReadUser = AdminUser,             # ✅ Admin only
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
        #current_user=current_user,
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




# routes.py



@router.post(
    "/add_country",
    status_code=status.HTTP_201_CREATED,
    summary="Create a country (admin only)",
)
async def create_country_route(
    data: CountryCreate,
    db: DBDep,
    #current_user: ReadUser = AdminUser,             # ✅ Admin only
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
        #current_user=current_user,
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









  
