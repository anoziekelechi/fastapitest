 @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return value.lower()
    
    
    @field_validator("surname", mode="before")
    @classmethod
    def validate_surname(cls, value: str) -> str:
        if not value:
            raise ValueError("Surname is required")
        user_input = value.strip()
        if not user_input:
            raise ValueError("Surname cannot be empty")
        if not re.fullmatch(r"[A-Za-z]+",user_input):
            raise ValueError("surname must contain only letters no space or numbers")
        return user_input.upper()
    
    
    @field_validator("othernames", mode="before")
    @classmethod
    def validate_othernames(cls, value: str) -> str:
        if not value:
            raise ValueError("othernames is required")
        user_input =re.sub(r"\s+", " ", value.strip()) #convert multiple line spaces to single
        if not user_input:
            raise ValueError("othernames cannot be empty")
        if not re.fullmatch(r"[A-Za-z]+", user_input):
            raise ValueError("Must contain only letters and spaces")
        return user_input.upper()


