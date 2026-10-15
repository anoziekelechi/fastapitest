class PaymentMethodUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_payment_option(v)
