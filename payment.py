def normalize_payment_option(value: str) -> str:
    """
    Normalize a single payment option selected from a dropdown menu.
    Trims whitespace, collapses internal multiple spaces, and forces uppercase.
    
    Valid:
        "momo  liberia"       → "MOMO LIBERIA"
        "bank transfer"       → "BANK TRANSFER"
        "opay"                → "OPAY"
    
    Invalid:
        ""                    → Error (empty selection)
        None                  → Error (missing selection)
    """
    if not value or not value.strip():
        raise ValueError("Payment option selection cannot be empty")
        
    if not isinstance(value, str):
        raise TypeError("Payment option must be a text string")
        
    # Collapse multiple internal spaces down to one, and capitalize everything
    return re.sub(r"\s+", " ", value.strip()).upper()



class PaymentMethodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=2, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Payment method name cannot be empty")
        return normalize_payment_option(v)
        

