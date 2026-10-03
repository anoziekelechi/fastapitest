#updated
ef validate_country_name(value: str) -> str:
    """
    Validate and normalize country name.
    
    Rules:
        - Cannot be empty
        - Letters and spaces only (no numbers or special characters)
        - Multiple consecutive spaces collapsed to single space
        - Leading/trailing spaces stripped
        - Each word capitalized (Title Case)
    
    Valid:
        "liberia"           → "Liberia"
        "south africa"      → "South Africa"
        "South   Africa"    → "South Africa"
        "guinea  bissau"    → "Guinea Bissau"
        "  Nigeria  "       → "Nigeria"
    
    Invalid:
        "South123"          → Error (contains number)
        "South@Africa"      → Error (special character)
        ""                  → Error (empty)
    """
    if not value or not value.strip():
        raise ValueError("Country name cannot be empty")
    
    # Strip leading/trailing whitespace
    stripped = value.strip()
    
    # Collapse multiple spaces to single space
    cleaned = re.sub(r" +", " ", stripped)
    
    # Letters and spaces only
    if not re.fullmatch(r"[A-Za-z]+( [A-Za-z]+)*", cleaned):
        raise ValueError(
            "Country name must contain only letters and spaces. "
            "No numbers or special characters allowed. "
            "Example: 'South Africa'"
        )
    
    # Title case: "south africa" → "South Africa"
    return cleaned.title()

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







#old
async def get_country_by_name(
    db: AsyncSession,
    name: str,
) -> Country | None:
    
    try:
        normalize_name=validate_country_name(name)
        result = await db.execute(
            select(Country).where(Country.name == normalize_name)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm name=%s", name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )

vs
async def get_country_by_name(
    db: AsyncSession,
    name: str,
) -> Country | None:
   
    try:
       
        result = await db.execute(
            select(Country).where(Country.name == name)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm name=%s", name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )
