class PaymentMethodUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_payment_option(v)


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
        
        
    # Collapse multiple internal spaces down to one, and capitalize everything
    return re.sub(r"\s+", " ", value.strip()).upper()

