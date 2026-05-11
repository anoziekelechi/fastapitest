field_validator('phone_number')
    @classmethod
    def validate_phone_number(cls, v: str | None) -> str | None:
        """Validate and format phone number to E.164 format."""
        if v is None:
            return v
        
        try:
            # Parse and validate
            parsed = phonenumbers.parse(v, None)
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError('Invalid phone number')
            
            # Format to E164 (+12025551234)
            return phonenumbers.format_number(
                parsed, 
                phonenumbers.PhoneNumberFormat.E164
            )
        except phonenumbers.NumberParseException:
            raise ValueError('Invalid phone number format')
