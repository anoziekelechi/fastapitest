

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

