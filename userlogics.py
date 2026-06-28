

# Standalone functions - NO cls, correct regex

def validate_surname(value: str) -> str:
    """
    Single word, letters only, no spaces.
    "Njoku" → "NJOKU"
    "Njoku Kelechi" → Error
    """
    if not value or not value.strip():
        raise ValueError("Surname cannot be empty")
    
    cleaned = value.strip()
    
    if not re.fullmatch(r"[A-Za-z]+", cleaned):
        if " " in cleaned:
            raise ValueError(
                "Surname must be a single word with no spaces. "
                "Example: 'Njoku' not 'Njoku Kelechi'"
            )
        raise ValueError(
            "Surname must contain only letters (A-Z). "
            "No numbers, spaces, or special characters allowed."
        )
    
    return cleaned.upper()


def validate_othernames(value: str) -> str:
    """
    Letters and spaces only. Multiple spaces collapsed to one.
    "Kelechi Chukwunonye" → "KELECHI CHUKWUNONYE"
    "Kelechi  Chukwunonye" → "KELECHI CHUKWUNONYE"
    """
    if not value or not value.strip():
        raise ValueError("Othernames cannot be empty")
    
    stripped = value.strip()
    cleaned = re.sub(r" +", " ", stripped)      # collapse spaces only
    
    if not re.fullmatch(r"[A-Za-z]+( [A-Za-z]+)*", cleaned):  # ✅ correct regex
        raise ValueError(
            "Othernames must contain only letters and spaces. "
            "No numbers or special characters allowed."
        )
    
    return cleaned.upper()


# Inside Pydantic models - ADD cls + @classmethod decorator

class CreateUser(BaseModel):
    @field_validator("surname", mode="before")
    @classmethod                               # ← cls required here
    def validate_user_surname(cls, v: str) -> str:
        return validate_surname(v)             # ← delegates to standalone function
    
    @field_validator("othernames", mode="before")
    @classmethod
    def validate_user_othernames(cls, v: str) -> str:
       
     return validate_othernames(v)  
     
# ← delegates to standalone function




def validate_name(value: str, field: str) -> str:
    """
    Routes to the correct name validator based on field label.
    
    Used ONLY by scripts/create_admin.py for interactive prompts.
    Schemas call validate_surname() and validate_othernames() directly.
    
    Args:
        value: The name string to validate
        field: "Surname" or "Othernames" (case-insensitive)
        
    Raises:
        ValueError: If field is not "surname" or "othernames"
    """
    field_lower = field.lower()
    
    if field_lower == "surname":
        return validate_surname(value)
    
    if field_lower == "othernames":
        return validate_othernames(value)
    
    # Should never reach here - but clear error if it does
    raise ValueError(
        f"Unknown field '{field}'. "
        f"Expected 'surname' or 'othernames'."
    )






