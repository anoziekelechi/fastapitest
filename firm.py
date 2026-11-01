from sqlmodel import SQLModel, Field
from pydantic import field_validator

class Item(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True) 
    
    # Stored as VARCHAR/TEXT in Postgres, normalized to at least 2 digits (e.g., "01")
    branch_no: str 

    @field_validator("branch_no", mode="before")
    @classmethod
    def validate_and_preserve_branch(cls, value: any) -> str:
        # 1. Ensure the input can safely turn into a positive integer
        try:
            int_value = int(value)
        except (ValueError, TypeError):
            raise ValueError("Branch number must contain only numbers.")
        
        if int_value <= 0:
            raise ValueError("Branch number must be a positive integer.")
            
        # 2. Format to a minimum of 2 digits with leading zeros
        # This converts 1 -> "01", 001 -> "01", and keeps 12 -> "12" or 123 -> "123"
        return f"{int_value:02d}"
