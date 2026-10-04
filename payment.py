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
        






import re
from pydantic import BaseModel, field_validator
from pydantic_core.core_schema import FieldValidationInfo

# --- Your Reusable Validator Function ---
def sanitize_alphanumeric_text(value: str, field_name: str = "Input") -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} cannot be empty")
        
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a text string")
    
    cleaned = re.sub(r"\s+", " ", value.strip())
    
    if not re.fullmatch(r"[A-Za-z0-9 ]+", cleaned):
        raise ValueError(
            f"{field_name} must contain only letters, numbers, and spaces. "
            f"Special characters and punctuation are not allowed."
        )
        
    return cleaned.upper()


# --- Your FastAPI / Pydantic Models ---
class ProductCreateSchema(BaseModel):
    name: str
    description: str
    
    @field_validator("name")
    @classmethod
    def validate_product_fields(cls, v: str, info: FieldValidationInfo) -> str:
        # info.field_name automatically passes "name" to your error message!
        return sanitize_alphanumeric_text(v, field_name=info.field_name)


class OrderCreateSchema(BaseModel):
    shipping_address: str
    billing_address: str
    
    # You can apply the exact same validator to multiple fields at once!
    @field_validator("shipping_address", "billing_address")
    @classmethod
    def validate_address_fields(cls, v: str, info: FieldValidationInfo) -> str:
        # info.field_name automatically becomes "shipping_address" or "billing_address"
        return sanitize_alphanumeric_text
