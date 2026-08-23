
"""
Shared field validators.

Usage:
    from api.core.validators import validate_international_phone, validate_whatsapp

    class MySchema(BaseModel):
        phone_number: str | None = None
        whatsapp: str | None = None

        @field_validator("phone_number", mode="before")
        @classmethod
        def validate_phone(cls, v: str | None) -> str | None:
            return validate_international_phone(v)

        @field_validator("whatsapp", mode="before")
        @classmethod
        def validate_whatsapp_number(cls, v: str | None) -> str | None:
            return validate_whatsapp(v)
"""
import phonenumbers


def validate_international_phone(value: str | None) -> str | None:
    """
    Validate and format phone number to E.164 international format.

    Rules:
        - None is accepted (optional field)
        - Must include country code with + prefix
        - Returns formatted E.164 string e.g. "+2348071234567"

    Valid:
        "+2317781900000"    → "+2317781900000"
        "+2348071234567"    → "+2348071234567"
        "+14155552671"      → "+14155552671"

    Invalid:
        "08071234567"       → Error (no country code)
        "2348071234567"     → Error (missing + prefix)
        "+999999999999999"  → Error (invalid number)
        "not-a-number"      → Error

    Args:
        value: Phone number string or None

    Returns:
        str | None: E.164 formatted phone number or None

    Raises:
        ValueError: If phone number is invalid
    """
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
    """
    Validate WhatsApp number and store WITHOUT '+' prefix.

    Flow:
        1. Validate as international phone number
        2. Strip '+' prefix before returning

    Rules:
        - None is accepted (optional field)
        - Must include country code with + prefix on input
        - Stored WITHOUT '+' in database

    Valid input → stored value:
        "+2317781900000"   → "2317781900000"
        "+2348071234567"   → "2348071234567"
        "+14155552671"     → "14155552671"

    Invalid:
        "08071234567"      → Error (no country code)
        "2348071234567"    → Error (missing + prefix)

    Args:
        value: WhatsApp number string or None

    Returns:
        str | None: Number without '+' prefix or None

    Raises:
        ValueError: If phone number is invalid
    """
    if value is None:
        return None

    # Reuse international phone validator
    validated = validate_international_phone(value)

    if validated is None:
        return None

    # Remove '+' prefix before storing
    # "+2348071234567" → "2348071234567"
    return validated.lstrip("+")
