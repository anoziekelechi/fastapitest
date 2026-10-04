import re


def validate_local_phone(value: str) -> str:
    """
    Validate and normalize a required local phone number.

    Rules:
        - Required: None and blank values are invalid.
        - Allows spaces, hyphens, parentheses, and periods
          as separators.
        - Must start with '0'.
        - Must contain 10 or 11 digits after normalization.
        - Rejects international phone number format.

    Returns:
        str: Normalized local phone number.

    Raises:
        ValueError: If the phone number is invalid.
    """
    if not value or not value.strip():
        raise ValueError("Phone number cannot be empty.")

    value = value.strip()

    if value.startswith("+"):
        raise ValueError(
            "This field accepts local format only. "
            "Use the international phone validator for "
            "numbers such as '+2347031246117'."
        )

    # Remove permitted separators.
    cleaned = re.sub(r"[\s().-]", "", value)

    if not cleaned.isdigit():
        raise ValueError(
            "Phone number must contain digits only, "
            "apart from spaces, hyphens, parentheses, "
            "or periods used as separators."
        )

    if not cleaned.startswith("0"):
        raise ValueError(
            "Local phone number must start with '0'."
        )

    if len(cleaned) not in (10, 11):
        raise ValueError(
            "Local phone number must contain "
            "10 or 11 digits."
        )

    return cleaned
