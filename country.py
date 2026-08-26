@overload
def normalize_email(value:str) -> str: ...
@overload
def normalize_email(value:str | None) -> None: ...

def normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().lower()



def validate_international_phone(value: str | None) -> str | None:
   
    if value is None:
        return None

    cleaned = value.strip()

    if not cleaned:
        return None

    # Must start with + for international format
    if not cleaned.startswith("+"):
        raise ValueError(
            "Phone number must be in international format starting with '+'. "
            "Example: '+2348071234567'"
        )

    try:
        parsed = phonenumbers.parse(cleaned, None)

        if not phonenumbers.is_valid_number(parsed):
            raise ValueError(
                f"'{cleaned}' is not a valid phone number. "
                f"Please check the country code and number."
            )

        # Format to E.164
        return phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.E164
        )

    except phonenumbers.NumberParseException:
        raise ValueError(
            "Invalid phone number format. "
            "Must be in international format e.g. '+2348071234567'"
        )


def validate_whatsapp(value: str | None) -> str | None:
   
    if value is None:
        return None

    # Reuse international phone validator
    validated = validate_international_phone(value)

    if validated is None:
        return None

    # Remove '+' prefix before storing
    # "+2348071234567" → "2348071234567"
    return validated.lstrip("+")






async def create_country(
    data: CountryCreate,
    db: AsyncSession,
    #current_user: ReadUser,         # ✅ Admin check done in route via require_admin
) -> dict:
    """
    Create a new country.

    Admin only.

    Flow:
        1. Check name uniqueness (case-insensitive)
        2. Check currency code uniqueness
        3. Create country record

    Args:
        data: Validated country data
        db: Database session
        current_user: Authenticated admin user

    Returns:
        dict: Success message

    Raises:
        HTTPException: 409 if name or currency code already exists
    """
    # only admin
    # if not current_user.is_admin:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Action not allowed"
    #     )
    # Check name uniqueness
    existing_name = await get_country_by_name(db, data.name)
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Country '{data.name}' already exists"
        )
    
    # Check currency code uniqueness
    existing_code = (
        await db.execute(
            select(Country).where(
                func.upper(Country.currency_code) == data.currency_code.upper()
            )
        )
    ).scalars().first()
    if existing_code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Currency code '{data.currency_code}' is already "
                   f"assigned to '{existing_code.name}'"
        )
        
    # check email support unique
    
    country = Country(
        name=data.name,
        currency_code=data.currency_code,
        email_support=data.email_support,
        whatsapp=data.whatsapp,
        slug=generate_slug(data.name),          # ← set directly
    )
    db.add(country)
    await db.commit()
    await db.refresh(country)
    
    # logger.info(
    #     f"Country '{country.name}' created by admin {current_user.id}"
    # )
    
    return {
        "message": f"Country '{country.name}' created successfully",
        "country": CountryRead.model_validate(country),
    }



async def read_single_country(
    db: AsyncSession,
    slug:str,
) -> CountryRead:
    # only admin soon
    """
    Get a single country by slug.

    Public - no authentication required.

    Args:
        country_id: Country ID to fetch
        db: Database session

    Returns:
        CountryRead: Country data

    Raises:
        HTTPException: 404 if not found
    """
    country = await get_country_by_slug(db, slug)
    
    
    return CountryRead.model_validate(country)


{
  "detail": [
    {
      "type": "value_error",
      "loc": [
        "body",
        "whatsapp"
      ],
      "msg": "Value error, Phone number must be in international format starting with '+'. Example: '+2348071234567'",
      "input": "string",
      "ctx": {
        "error": {}
      }
    }
  ]
}


class CountryCreate(BaseModel):
    """Schema for creating a country."""
    model_config = ConfigDict(extra="forbid")
    
    name: str
    currency_code: str
    whatsapp: str | None = None
    email_support: str | None = None
    
    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_country_name(v)
    
    @field_validator("currency_code", mode="before")
    @classmethod
    def validate_code(cls, v: str) -> str:
        return validate_currency_code(v)
     
    @field_validator("whatsapp", mode="before")
    @classmethod
    def validate_whatsapp(cls, v: str| None) -> str | None:
        return validate_whatsapp(v)
     
    @field_validator("email_support", mode="before")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        return normalize_email(v)





# api/admin/script.py or a one-off script

async def seed_slugs():
    """Generate slugs for countries that don't have one."""
    from api.core.slug import generate_slug
    
    async with AsyncSessionFactory() as db:
        result = await db.execute(
            select(Country).where(Country.slug == None)
        )
        countries = result.scalars().all()
        
        for country in countries:
            country.slug = generate_slug(country.name, country.id)
            db.add(country)
        
        await db.commit()
        print(f"✅ Generated slugs for {len(countries)} countries")

asyncio.run(seed_slugs())


docker compose exec backend python api/admin/seed_slugs.py

async def delete_country(
    db: AsyncSession,
    slug: str,
    current_user: ReadUser,
) -> dict:
    """
    Delete a country by slug.

    Admin only.

    DB behavior:
        - Users: country_id set to NULL (ON DELETE SET NULL)
        - Offices: deleted automatically (ON DELETE CASCADE)
    """
    country = await get_country_by_slug(db, slug)

    country_name = country.name
    country_id = country.id

    await db.delete(country)
    await db.commit()

    logger.info(
        f"Country '{country_name}' (id={country_id}) "
        f"deleted by admin {current_user.id}"
    )

    return {"message": f"Country '{country_name}' deleted successfully"}



@router.delete(
    "/{slug}",
    status_code=status.HTTP_200_OK,
    summary="Delete country by slug (admin only)",
)
async def delete_country_route(
    slug: str,
    db: AsyncSession = Depends(get_session),
    current_user: ReadUser = Depends(require_permission("can_manage_countries")),
    # or your admin dependency
) -> dict:
    return await delete_country(
        db=db,
        slug=slug,
        current_user=current_user,
    )





async def update_country(
    slug: str,
    data: CountryUpdate,
    db: AsyncSession,
    # current_user: ReadUser,
) -> CountryRead:
    """Update an existing country (identified by slug)."""

    # await has_permission(
    #     user=current_user,
    #     required_perm=Permissions.MANAGE_COUNTRIES,
    # )

    country = await get_country_by_slug(db, slug)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country with slug '{slug}' not found",
        )

    # Reject completely empty payloads
    if all(
        v is None
        for v in [
            data.name,
            data.currency_code,
            data.whatsapp,
            data.email_support,
        ]
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    updated_fields: list[str] = []

    # Name (+ regenerate slug)
    if data.name is not None and data.name != country.name:
        existing = await get_country_by_name(db, data.name)
        if existing and existing.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists",
            )

        country.name = data.name
        country.slug = generate_slug(data.name)
        updated_fields.extend(["name", "slug"])

    # Currency code
    if data.currency_code is not None and data.currency_code != country.currency_code:
        existing_code = (
            await db.execute(
                select(Country).where(
                    func.upper(Country.currency_code) == data.currency_code.upper()
                )
            )
        ).scalars().first()

        if existing_code and existing_code.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Currency code '{data.currency_code}' is already "
                    f"assigned to '{existing_code.name}'"
                ),
            )

        country.currency_code = data.currency_code
        updated_fields.append("currency_code")

    # WhatsApp (unique)
    if data.whatsapp is not None and data.whatsapp != country.whatsapp:
        existing_whatsapp = (
            await db.execute(
                select(Country).where(Country.whatsapp == data.whatsapp)
            )
        ).scalars().first()

        if existing_whatsapp and existing_whatsapp.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"WhatsApp number '{data.whatsapp}' is already "
                    f"assigned to '{existing_whatsapp.name}'"
                ),
            )

        country.whatsapp = data.whatsapp
        updated_fields.append("whatsapp")

    # Support email (unique)
    if data.email_support is not None and data.email_support != country.email_support:
        existing_email = (
            await db.execute(
                select(Country).where(Country.email_support == data.email_support)
            )
        ).scalars().first()

        if existing_email and existing_email.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Support email '{data.email_support}' is already "
                    f"assigned to '{existing_email.name}'"
                ),
            )

        country.email_support = data.email_support
        updated_fields.append("email_support")

    # Nothing actually changed
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes detected – all supplied values are identical to the current ones",
        )

    db.add(country)
    await db.commit()
    await db.refresh(country)

    # logger.info(
    #     f"Country '{country.name}' (slug={country.slug}) updated by id={current_user.id}. "
    #     f"Fields: {', '.join(updated_fields)}"
    # )

    return CountryRead.model_validate(country)


